#!/usr/bin/env python3
"""Run execution-grounded benchmark cases against Superset native AI."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


SCORER_REVISION = "answer-first-1dp-v2"


class SupersetClient:
    """Small authenticated client for the APIs exercised by the benchmark."""

    def __init__(self, base_url: str, public_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        _, login = self.request(
            "POST",
            "/api/v1/security/login",
            payload={
                "username": username,
                "password": password,
                "provider": "db",
                "refresh": True,
            },
        )
        self.token = str(login["access_token"])
        self.refresh_token = str(login["refresh_token"])
        _, csrf = self.request("GET", "/api/v1/security/csrf_token/", auth=True)
        self.csrf = str(csrf["result"])

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        auth: bool = False,
        csrf: bool = False,
        timeout: int = 60,
        retry_auth: bool = True,
    ) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth and hasattr(self, "token"):
            headers["Authorization"] = f"Bearer {self.token}"
        if csrf and hasattr(self, "csrf"):
            headers["X-CSRFToken"] = self.csrf
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read()
                if not raw:
                    return response.status, {}
                try:
                    return response.status, json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return response.status, {"raw": raw.decode("utf-8", "replace")}
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            if error.code == 401 and auth and retry_auth and hasattr(self, "refresh_token"):
                self._refresh_access_token()
                return self.request(
                    method,
                    path,
                    payload=payload,
                    auth=auth,
                    csrf=csrf,
                    timeout=timeout,
                    retry_auth=False,
                )
            raise RuntimeError(f"{method} {path} -> {error.code}: {detail}") from error

    def _refresh_access_token(self) -> None:
        request = Request(
            self.base_url + "/api/v1/security/refresh",
            headers={"Authorization": f"Bearer {self.refresh_token}"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Superset JWT refresh failed: {error}") from error
        self.token = str(body["access_token"])

    def create_thread(self, title: str, profile: str) -> str:
        status, body = self.request(
            "POST",
            "/api/v1/ai/thread/",
            payload={"title": title, "agent_key": profile},
            auth=True,
            csrf=True,
        )
        if status != 201:
            raise RuntimeError(f"Thread creation returned {status}")
        return str(body["result"]["uuid"])

    def run_turn(
        self,
        thread_uuid: str,
        profile: str,
        prompt: str,
        page_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "content": prompt,
            "request_id": f"native-ai-bench-{uuid.uuid4()}",
            "agent_key": profile,
        }
        if page_context is not None:
            payload["page_context"] = page_context
        status, accepted = self.request(
            "POST",
            f"/api/v1/ai/thread/{thread_uuid}/message",
            payload=payload,
            auth=True,
            csrf=True,
        )
        if status != 202:
            raise RuntimeError(f"Turn submission returned {status}")
        run_id = str(accepted["result"]["run_id"])
        events, stream_errors = self._stream(thread_uuid, run_id)
        _, thread = self.request(
            "GET", f"/api/v1/ai/thread/{thread_uuid}", auth=True
        )
        messages = list(thread["result"]["messages"])
        assistant = next(
            (
                item
                for item in reversed(messages)
                if item.get("role") == "assistant"
                and (item.get("extra") or {}).get("run_id") == run_id
            ),
            None,
        )
        if assistant is None:
            raise RuntimeError(f"Run {run_id} has no persisted assistant message")
        return {
            "run_id": run_id,
            "thread_uuid": thread_uuid,
            "events": events,
            "stream_errors": stream_errors,
            "assistant": assistant,
        }

    def _stream(self, thread_uuid: str, run_id: str) -> tuple[list[str], list[str]]:
        path = f"/api/v1/ai/thread/{thread_uuid}/stream?" + urlencode(
            {"run_id": run_id}
        )
        request = Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        events: list[str] = []
        errors: list[str] = []
        with self.opener.open(request, timeout=360) as response:
            event_name = "message"
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line:
                    events.append(event_name)
                    if event_name == "error":
                        errors.append("\n".join(data_lines))
                    event_name = "message"
                    data_lines = []
        return events, errors

    def execute_sql(self, database_id: int, sql: str) -> list[dict[str, Any]]:
        status, body = self.request(
            "POST",
            "/api/v1/sqllab/execute/",
            payload={
                "database_id": database_id,
                "sql": sql,
                "client_id": uuid.uuid4().hex[:10],
                "queryLimit": 1000,
                "runAsync": False,
            },
            auth=True,
            csrf=True,
            timeout=180,
        )
        if status != 200 or body.get("status") != "success":
            raise RuntimeError(f"Golden SQL failed: {body}")
        return list(body.get("data") or [])

    def get_asset(self, kind: str, asset_id: int) -> dict[str, Any]:
        _, body = self.request(
            "GET", f"/api/v1/{kind}/{asset_id}", auth=True
        )
        return dict(body["result"])

    def delete_asset(self, kind: str, asset_id: int) -> None:
        self.request(
            "DELETE",
            f"/api/v1/{kind}/{asset_id}",
            auth=True,
            csrf=True,
        )

    def delete_thread(self, thread_uuid: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/ai/thread/{thread_uuid}",
            auth=True,
            csrf=True,
        )

    def asset_url(self, kind: str, asset_id: int) -> str:
        return f"{self.public_url}/{kind}/{asset_id}/"


class LangfuseBridge:
    """Publish benchmark links and scores onto Superset's existing traces."""

    def __init__(self, dataset_name: str, run_name: str, description: str):
        from langfuse import Langfuse

        self.client = Langfuse()
        self.dataset_name = dataset_name
        self.run_name = run_name
        # Langfuse server 3.177 omits the newer SDK's ``media_references``
        # response field. Reuse the SDK's authenticated transport and parse
        # these two write responses as plain JSON until the server is upgraded.
        self._transport = (
            self.client.api.dataset_items._raw_client._client_wrapper.httpx_client
        )
        try:
            self.client.api.datasets.get(dataset_name)
        except Exception as error:  # SDK exposes generated exception classes.
            if getattr(error, "status_code", None) != 404:
                raise
            self.client.create_dataset(name=dataset_name, description=description)

    def prepare_item(self, case: dict[str, Any], version: str) -> str:
        item_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{self.dataset_name}:{case['id']}")
        )
        expected = {
            key: case[key]
            for key in (
                "database_id",
                "golden_sql",
                "facts",
                "allow_multiple_sql_calls",
                "answer_must_not_match",
                "required_tools",
                "forbidden_successful_tools",
                "asset",
                "safety_case",
            )
            if key in case
        }
        self._post(
            "api/public/dataset-items",
            {
                "datasetName": self.dataset_name,
                "id": item_id,
                "input": {
                    "prompt": case["prompt"],
                    "profile": case["profile"],
                    "page_context": case.get("page_context"),
                },
                "expectedOutput": expected,
                "metadata": {
                    "case_id": case["id"],
                    "category": case["category"],
                    "tags": case.get("tags", []),
                    "benchmark_version": version,
                },
            },
        )
        return item_id

    def sync_result(
        self,
        item_id: str,
        result: dict[str, Any],
        version: str,
    ) -> dict[str, Any]:
        trace_id = self.client.create_trace_id(seed=result["run_id"])
        observations = self._wait_for_trace(trace_id)
        run_item = self._post(
            "api/public/dataset-run-items",
            {
                "runName": self.run_name,
                "runDescription": "Superset native AI execution-grounded benchmark",
                "metadata": {
                    "case_id": result["case_id"],
                    "benchmark_version": version,
                },
                "datasetItemId": item_id,
                "traceId": trace_id,
            },
        )
        dataset_run_id = str(run_item["datasetRunId"])
        scores: dict[str, float] = {
            "overall": float(result["passed"]),
            "latency_seconds": float(result["latency_seconds"]),
            "tool_success_rate": float(result["tool_success_rate"]),
        }
        for name, value in result["dimensions"].items():
            if value is not None:
                scores[name] = float(value)
        score_names = [f"bench.{name}" for name in scores]
        for name, value in scores.items():
            score_name = f"bench.{name}"
            self._post(
                "api/public/scores",
                {
                    "id": _langfuse_score_id(trace_id, score_name),
                    # Server 3.177 accepts exactly one target. The dataset run
                    # item above already links this trace into the experiment.
                    "traceId": trace_id,
                    "name": score_name,
                    "value": value,
                    "dataType": "NUMERIC",
                    "comment": f"case={result['case_id']} benchmark={version}",
                    "metadata": {"case_id": result["case_id"]},
                },
            )
        self._wait_for_scores(trace_id, set(score_names))
        return {
            "trace_id": trace_id,
            "observation_count": observations,
            "dataset_run_id": dataset_run_id,
            "score_names": score_names,
        }

    def _wait_for_trace(self, trace_id: str) -> int:
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                trace = self.client.api.trace.get(trace_id)
                names = {observation.name for observation in trace.observations}
                if "superset.ai.run" in names and trace.output is not None:
                    return len(trace.observations)
            except Exception as error:  # Event ingestion is eventually consistent.
                last_error = error
            time.sleep(1)
        raise RuntimeError(
            f"Langfuse trace {trace_id} was not complete after 30s: {last_error}"
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._transport.request(path, method="POST", json=payload)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"Langfuse POST {path} -> {response.status_code}: {response.text}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"Langfuse POST {path} returned a non-object")
        return body

    def _wait_for_scores(self, trace_id: str, expected: set[str]) -> None:
        deadline = time.monotonic() + 15
        found: set[str] = set()
        while time.monotonic() < deadline:
            found = set()
            for name in expected:
                score_id = _langfuse_score_id(trace_id, name)
                response = self._transport.request(
                    f"api/public/scores/{score_id}", method="GET"
                )
                if 200 <= response.status_code < 300:
                    body = response.json()
                    if (
                        body.get("id") == score_id
                        and body.get("traceId") == trace_id
                        and body.get("name") == name
                    ):
                        found.add(name)
            if expected <= found:
                return
            time.sleep(1)
        raise RuntimeError(
            f"Langfuse score read-back incomplete: missing={sorted(expected - found)}"
        )

    def close(self) -> None:
        self.client.shutdown()


def _langfuse_score_id(trace_id: str, score_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{trace_id}:{score_name}"))


class DatabaseLoadGate:
    """Pause database-touching cases while the target SQL Server DB is busy."""

    def __init__(
        self,
        client: SupersetClient,
        superset_database_id: int,
        server_database_name: str,
        poll_seconds: float,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", server_database_name):
            raise ValueError("Unsafe SQL Server database name for load gate")
        self.client = client
        self.superset_database_id = superset_database_id
        self.server_database_name = server_database_name
        self.poll_seconds = poll_seconds
        self.confirmed = False

    def wait(self) -> float:
        started = time.monotonic()
        required_idle_polls = 1 if self.confirmed else 2
        idle_polls = 0
        while idle_polls < required_idle_polls:
            rows = self.client.execute_sql(
                self.superset_database_id,
                f"""
                SELECT
                  (SELECT COUNT(*) FROM sys.databases
                   WHERE name = '{self.server_database_name}') AS database_found,
                  COUNT(*) AS active_requests,
                  COALESCE(SUM(logical_reads), 0) AS logical_reads,
                  COALESCE(SUM(reads), 0) AS reads,
                  SUM(CASE WHEN command IN ('INSERT','UPDATE','DELETE','MERGE')
                           THEN 1 ELSE 0 END) AS write_requests
                FROM sys.dm_exec_requests
                WHERE session_id <> @@SPID
                  AND database_id = (SELECT database_id FROM sys.databases
                                     WHERE name = '{self.server_database_name}')
                """,
            )
            if len(rows) != 1 or int(rows[0].get("database_found") or 0) != 1:
                raise RuntimeError(
                    f"Load gate cannot resolve SQL Server database {self.server_database_name}"
                )
            load = rows[0]
            idle = int(load.get("active_requests") or 0) == 0
            if idle:
                idle_polls += 1
            else:
                self.confirmed = False
                idle_polls = 0
                required_idle_polls = 2
            if idle_polls < required_idle_polls:
                print(
                    "  DB LOAD WAIT "
                    f"active={load.get('active_requests')} "
                    f"writes={load.get('write_requests') or 0} "
                    f"logical_reads={load.get('logical_reads')} "
                    f"reads={load.get('reads')}",
                    flush=True,
                )
                time.sleep(self.poll_seconds)
        self.confirmed = True
        return round(time.monotonic() - started, 3)


def _tool_payload(call: dict[str, Any]) -> dict[str, Any]:
    output = call.get("output")
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
    display = call.get("display")
    return display if isinstance(display, dict) else {}


def _last_successful_call(
    calls: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    return next(
        (
            call
            for call in reversed(calls)
            if call.get("name") == name and call.get("ok") is True
        ),
        None,
    )


def _scalars(value: Any) -> list[Any]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, list):
        return [item for value_item in value for item in _scalars(value_item)]
    if isinstance(value, dict):
        return [item for value_item in value.values() for item in _scalars(value_item)]
    return [value]


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _canonical_text(value: Any) -> str:
    text = " ".join(str(value).strip().casefold().split())
    if match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[t ].*)?", text):
        return f"date:{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if match := re.fullmatch(r"(\d{2})[./](\d{2})[./](\d{4})", text):
        return f"date:{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return text


def _value_matches(actual: Any, expected: Any, tolerance: Decimal) -> bool:
    actual_number = _decimal(actual)
    expected_number = _decimal(expected)
    if actual_number is not None and expected_number is not None:
        return _close_enough(actual_number, expected_number, tolerance)
    return _canonical_text(actual) == _canonical_text(expected)


def _number_pattern(value: Any) -> str:
    number = Decimal(str(value))
    plain = format(number, "f")
    negative = plain.startswith("-")
    sign = r"[-−]" if negative else ""
    plain = plain.lstrip("-")
    integer, _, fraction = plain.partition(".")
    groups: list[str] = []
    first = len(integer) % 3 or 3
    groups.append(integer[:first])
    groups.extend(integer[index : index + 3] for index in range(first, len(integer), 3))
    integer_pattern = r"[\s\u00a0,.]?".join(map(re.escape, groups))
    significant_fraction = fraction.rstrip("0")
    fraction_pattern = (
        rf"[.,]{re.escape(significant_fraction)}0*"
        if significant_fraction
        else r"(?:[.,]0+)?"
    )
    prefix = r"(?<!\d)" if negative else r"(?<![\d\-−])"
    return rf"{prefix}{sign}{integer_pattern}{fraction_pattern}(?!\d|[.,]\d)"


def _answer_text(answer: str) -> str:
    return re.sub(r"```.*?```", " ", answer, flags=re.DOTALL).casefold().replace(
        "\u202f", " "
    )


def _answer_has_number(answer: str, value: Any) -> bool:
    return bool(re.search(_number_pattern(value), _answer_text(answer)))


def _value_pattern(value: Any) -> str:
    if _decimal(value) is not None:
        return _number_pattern(value)
    canonical = _canonical_text(value)
    if canonical.startswith("date:"):
        year, month, day = canonical.removeprefix("date:").split("-")
        month_name = {
            "01": "января",
            "02": "февраля",
            "03": "марта",
            "04": "апреля",
            "05": "мая",
            "06": "июня",
            "07": "июля",
            "08": "августа",
            "09": "сентября",
            "10": "октября",
            "11": "ноября",
            "12": "декабря",
        }[month]
        return (
            rf"(?:{year}-{month}-{day}|{day}[./]{month}[./]{year}|"
            rf"{int(day)}\s+{month_name}\s+{year}(?:\s+года|\s+г\.)?)"
        )
    return r"\s+".join(re.escape(part) for part in canonical.split())


def _has_rounded_number(text: str, value: Any) -> bool:
    expected = _decimal(value)
    if expected is None:
        return False
    expected = expected.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    for token in re.findall(r"(?<!\d)[-−]?\d+(?:[.,]\d+)?(?!\d)", text):
        actual = _decimal(token.replace("−", "-").replace(",", "."))
        if actual is not None and actual.quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        ) == expected:
            return True
    return False


def _answer_has_value(answer: str, value: Any) -> bool:
    text = _answer_text(answer)
    return bool(re.search(_value_pattern(value), text)) or _has_rounded_number(
        text, value
    )


def _answer_has_fact(
    answer: str,
    labels: list[str],
    value: Any,
) -> bool:
    text = _answer_text(answer)
    expected = re.compile(_value_pattern(value))
    if _canonical_text(value).startswith("date:"):
        return bool(expected.search(text))
    for label in labels:
        for match in re.finditer(label.casefold(), text):
            window = text[max(0, match.start() - 80) : match.end() + 180]
            if expected.search(window) or _has_rounded_number(window, value):
                return True
    return False


def _sql_pattern_text(sql: str) -> str:
    # SQL Server's Unicode literal prefix does not change filter semantics.
    return re.sub(r"\bN(?=')", "", sql, flags=re.IGNORECASE)


def _answer_matches_any(answer: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, answer, re.IGNORECASE) for pattern in patterns)


def _close_enough(actual: Decimal, expected: Decimal, tolerance: Decimal) -> bool:
    return abs(actual - expected) <= tolerance


def _json_object(value: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(value, dict):
        return value, True
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}, False
        if isinstance(parsed, dict):
            return parsed, True
    return {}, False


def _title_matches(actual: Any, expectation: dict[str, Any]) -> bool:
    title = str(actual or "")
    if "title_equals" in expectation:
        return title == str(expectation["title_equals"])
    return str(expectation.get("title_contains", "")).casefold() in title.casefold()


def _column_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [name for item in value for name in _column_names(item)]
    if not isinstance(value, dict):
        return []
    if value.get("column") is not None:
        names = _column_names(value["column"])
        if names:
            return names
    for key in ("column_name", "name"):
        if isinstance(value.get(key), str):
            return [value[key]]
    return []


def _simple_filters(value: Any) -> list[dict[str, Any]]:
    filters = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("operator", "")).upper() == "TEMPORAL_RANGE":
            continue
        filters.append(
            {"subject": item.get("subject"), "comparator": item.get("comparator")}
        )
    return sorted(
        filters,
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
    )


def _sort_by(value: Any) -> list[dict[str, Any]]:
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError:
                continue
        if isinstance(item, list) and len(item) == 2:
            result.append({"column": item[0], "ascending": bool(item[1])})
    return result


def _chart_param_evidence(
    params: dict[str, Any], expected: dict[str, Any]
) -> tuple[dict[str, bool], dict[str, Any]]:
    checks: dict[str, bool] = {}
    actual: dict[str, Any] = {}
    if "x_axis" in expected:
        names = _column_names(params.get("x_axis"))
        actual["x_axis"] = names[0] if names else None
        checks["x_axis"] = actual["x_axis"] == expected["x_axis"]
    if "metric_columns" in expected:
        metrics = params.get("metrics")
        if not metrics and params.get("metric") is not None:
            metrics = [params["metric"]]
        actual["metric_columns"] = sorted(_column_names(metrics))
        checks["metric_columns"] = actual["metric_columns"] == sorted(
            expected["metric_columns"]
        )
    if "table_columns" in expected:
        actual["table_columns"] = _column_names(
            params.get("all_columns") or params.get("columns")
        )
        checks["table_columns"] = sorted(actual["table_columns"]) == sorted(
            expected["table_columns"]
        )
    if "groupby_columns" in expected:
        actual["groupby_columns"] = sorted(_column_names(params.get("groupby")))
        checks["groupby_columns"] = actual["groupby_columns"] == sorted(
            expected["groupby_columns"]
        )
    if "filters" in expected:
        actual["filters"] = _simple_filters(params.get("adhoc_filters"))
        checks["filters"] = actual["filters"] == _simple_filters(expected["filters"])
    if "stacked" in expected:
        actual["stacked"] = bool(params.get("stack"))
        checks["stacked"] = actual["stacked"] is bool(expected["stacked"])
    if "sort_by" in expected:
        actual["sort_by"] = _sort_by(params.get("order_by_cols"))
        checks["sort_by"] = actual["sort_by"] == expected["sort_by"]
    return checks, actual


def _validate_sql_case(
    client: SupersetClient,
    case: dict[str, Any],
    calls: list[dict[str, Any]],
    answer: str,
) -> tuple[bool, bool, dict[str, Any]]:
    frozen_rows = case.get("_golden_rows")
    golden_rows = (
        list(frozen_rows)
        if isinstance(frozen_rows, list)
        else client.execute_sql(int(case["database_id"]), case["golden_sql"])
    )
    if len(golden_rows) != 1:
        raise RuntimeError(
            f"Golden SQL for {case['id']} must return one row, got {len(golden_rows)}"
        )
    golden = golden_rows[0]
    sql_calls = [
        call
        for call in calls
        if call.get("name") == "execute_sql" and call.get("ok") is True
    ]
    if not sql_calls:
        return False, False, {"golden_rows": golden_rows, "reason": "no_execute_sql"}
    candidates: list[dict[str, Any]] = []
    for index, call in enumerate(sql_calls):
        raw_sql = str((call.get("arguments") or {}).get("sql") or "")
        pattern_sql = _sql_pattern_text(raw_sql)
        payload = _tool_payload(call)
        actual_values = _scalars(payload.get("rows", []))
        pattern_checks = {
            pattern: bool(re.search(pattern, pattern_sql, re.IGNORECASE | re.DOTALL))
            for pattern in case.get("sql_must_match", [])
        }
        required_fact_hits = 0
        for fact in case.get("facts", []):
            if not fact.get("tool_required", True):
                continue
            expected = golden[fact["golden_key"]]
            tolerance = Decimal(str(fact.get("tolerance", "0")))
            required_fact_hits += any(
                _value_matches(actual, expected, tolerance)
                for actual in actual_values
            )
        candidates.append(
            {
                "index": index,
                "raw_sql": raw_sql,
                "payload": payload,
                "actual_values": actual_values,
                "pattern_checks": pattern_checks,
                "required_fact_hits": required_fact_hits,
            }
        )
    if case.get("allow_multiple_sql_calls"):
        combined_sql = "\n\n".join(item["raw_sql"] for item in candidates)
        combined_rows = [
            row
            for item in candidates
            for row in (
                item["payload"].get("rows", [])
                if isinstance(item["payload"].get("rows", []), list)
                else []
            )
        ]
        pattern_sql = _sql_pattern_text(combined_sql)
        selected = {
            "index": "all",
            "raw_sql": combined_sql,
            "payload": {"rows": combined_rows},
            "actual_values": [
                value for item in candidates for value in item["actual_values"]
            ],
            "pattern_checks": {
                pattern: bool(
                    re.search(pattern, pattern_sql, re.IGNORECASE | re.DOTALL)
                )
                for pattern in case.get("sql_must_match", [])
            },
        }
    else:
        # A verification COUNT may legitimately follow the business query. Pick
        # one self-contained query with the most expected facts; never merge cells
        # from unrelated queries into a synthetic pass.
        selected = max(
            candidates,
            key=lambda item: (
                item["required_fact_hits"],
                sum(item["pattern_checks"].values()),
                item["index"],
            ),
        )
    raw_sql = selected["raw_sql"]
    payload = selected["payload"]
    actual_values = selected["actual_values"]
    pattern_checks = selected["pattern_checks"]
    fact_evidence: list[dict[str, Any]] = []
    tool_facts_ok = True
    answer_facts_ok = True
    facts = case.get("facts", [])
    for fact in facts:
        key = fact["golden_key"]
        if key not in golden:
            raise RuntimeError(f"Golden SQL for {case['id']} lacks column {key}")
        expected = golden[key]
        tolerance = Decimal(str(fact.get("tolerance", "0")))
        tool_required = fact.get("tool_required", True)
        answer_required = fact.get("answer_required", True)
        in_tool = any(
            _value_matches(actual, expected, tolerance) for actual in actual_values
        )
        near_label = _answer_has_fact(answer, fact["labels"], expected)
        in_answer = _answer_has_value(answer, expected)
        if tool_required and not in_tool:
            tool_facts_ok = False
        if answer_required and not in_answer:
            answer_facts_ok = False
        fact_evidence.append(
            {
                "golden_key": key,
                "expected": str(expected),
                "tool_required": tool_required,
                "answer_required": answer_required,
                "found_in_tool_result": in_tool,
                "found_in_answer": in_answer,
                "found_near_label": near_label,
            }
        )
    sql_ok = bool(raw_sql) and tool_facts_ok
    return sql_ok, answer_facts_ok, {
        "golden_source": "frozen" if isinstance(frozen_rows, list) else "live_sql",
        "golden_rows": golden_rows,
        "agent_sql": raw_sql,
        "agent_rows": payload.get("rows", []),
        "selected_tool_call_index": selected["index"],
        "sql_evidence_mode": (
            "all_successful_calls"
            if case.get("allow_multiple_sql_calls")
            else "single_best_call"
        ),
        "successful_execute_sql_count": len(sql_calls),
        "supporting_sql": [item["raw_sql"] for item in candidates],
        "sql_pattern_checks": pattern_checks,
        "facts": fact_evidence,
    }


def _validate_chart(
    client: SupersetClient,
    expectation: dict[str, Any],
    calls: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any], dict[str, list[int]]]:
    chart_calls = [
        call
        for call in calls
        if call.get("name") == "generate_chart" and call.get("ok") is True
    ]
    payloads = [_tool_payload(call) for call in chart_calls]
    chart_items = [
        (call, payload, int(payload["chart_id"]))
        for call, payload in zip(chart_calls, payloads)
        if payload.get("chart_id")
    ]
    chart_ids = [item[2] for item in chart_items]
    snapshots: list[dict[str, Any]] = []
    checks: dict[str, bool] = {
        "chart_count_min": len(chart_ids) >= int(expectation.get("min_count", 1))
    }
    if "max_count" in expectation:
        checks["chart_count_max"] = len(chart_ids) <= int(expectation["max_count"])
    for call, payload, chart_id in chart_items:
        prefix = f"chart_{chart_id}"
        try:
            detail = client.get_asset("chart", chart_id)
        except Exception as error:
            checks[f"{prefix}.retrievable"] = False
            snapshots.append({"id": chart_id, "error": str(error)})
            continue
        warnings = [str(item) for item in payload.get("warnings", [])]
        warning_patterns = expectation.get("allowed_warning_patterns", [])
        warnings_ok = bool(expectation.get("allow_warnings")) or all(
            any(re.search(pattern, warning, re.IGNORECASE) for pattern in warning_patterns)
            for warning in warnings
        )
        params, params_json_ok = _json_object(detail.get("params"))
        param_checks, actual_params = _chart_param_evidence(
            params, expectation.get("expected_params", {})
        )
        checks.update(
            {
                f"{prefix}.retrievable": True,
                f"{prefix}.dataset_id": int(detail.get("datasource_id") or -1)
                == int(expectation["dataset_id"]),
                f"{prefix}.viz_type": detail.get("viz_type")
                in expectation.get("viz_types", []),
                f"{prefix}.title": _title_matches(detail.get("slice_name"), expectation),
                f"{prefix}.saved": bool((call.get("arguments") or {}).get("save_chart")),
                f"{prefix}.warnings": warnings_ok,
                f"{prefix}.params_json": params_json_ok,
                **{
                    f"{prefix}.params.{name}": passed
                    for name, passed in param_checks.items()
                },
            }
        )
        snapshots.append(
            {
                "id": chart_id,
                "slice_name": detail.get("slice_name"),
                "viz_type": detail.get("viz_type"),
                "datasource_id": detail.get("datasource_id"),
                "params": detail.get("params"),
                "actual_expected_params": actual_params,
                "param_checks": param_checks,
                "warnings": warnings,
                "url": client.asset_url("chart", chart_id),
            }
        )
    return all(checks.values()), {
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "charts": snapshots,
    }, {
        "chart": chart_ids,
        "dashboard": [],
    }


def _dashboard_layout_evidence(
    position: dict[str, Any], expected_ids: list[int], auto_layout: bool
) -> tuple[dict[str, bool], list[list[int]], list[int]]:
    root = position.get("ROOT_ID")
    grid = position.get("GRID_ID")
    checks = {
        "layout_version": position.get("DASHBOARD_VERSION_KEY") == "v2",
        "layout_root": isinstance(root, dict)
        and root.get("type") == "ROOT"
        and root.get("children") == ["GRID_ID"],
        "layout_grid": isinstance(grid, dict)
        and grid.get("type") == "GRID"
        and grid.get("parents") == ["ROOT_ID"],
    }
    rows: list[list[int]] = []
    tree_ids: list[int] = []
    tree_ok = checks["layout_root"] and checks["layout_grid"]
    row_ids = grid.get("children", []) if isinstance(grid, dict) else []
    tree_ok = tree_ok and isinstance(row_ids, list) and bool(row_ids)
    for row_id in row_ids if isinstance(row_ids, list) else []:
        row = position.get(row_id)
        column_ids = row.get("children", []) if isinstance(row, dict) else []
        row_ok = (
            isinstance(row, dict)
            and row.get("type") == "ROW"
            and row.get("parents") == ["ROOT_ID", "GRID_ID"]
            and isinstance(column_ids, list)
            and 1 <= len(column_ids) <= 2
        )
        row_chart_ids: list[int] = []
        for column_id in column_ids if isinstance(column_ids, list) else []:
            column = position.get(column_id)
            children = column.get("children", []) if isinstance(column, dict) else []
            column_ok = (
                isinstance(column, dict)
                and column.get("type") == "COLUMN"
                and column.get("parents") == ["ROOT_ID", "GRID_ID", row_id]
                and isinstance(children, list)
                and len(children) == 1
                and int((column.get("meta") or {}).get("width") or 0) > 0
            )
            chart = position.get(children[0]) if column_ok else None
            meta = chart.get("meta") if isinstance(chart, dict) else {}
            try:
                chart_id = int((meta or {}).get("chartId"))
            except (TypeError, ValueError):
                chart_id = -1
            chart_ok = (
                isinstance(chart, dict)
                and chart.get("type") == "CHART"
                and chart.get("id") == f"CHART-{chart_id}"
                and chart.get("parents")
                == ["ROOT_ID", "GRID_ID", row_id, column_id]
                and int((meta or {}).get("height") or 0) > 0
                and int((meta or {}).get("width") or 0) > 0
                and chart_id >= 0
            )
            row_ok = row_ok and column_ok and chart_ok
            if chart_ok:
                row_chart_ids.append(chart_id)
                tree_ids.append(chart_id)
        tree_ok = tree_ok and row_ok
        rows.append(row_chart_ids)
    all_chart_ids = [
        int(item["meta"]["chartId"])
        for item in position.values()
        if isinstance(item, dict)
        and item.get("type") == "CHART"
        and (item.get("meta") or {}).get("chartId") is not None
    ]
    checks["layout_tree"] = tree_ok
    checks["layout_chart_ids_unique"] = len(tree_ids) == len(set(tree_ids))
    checks["layout_covers_all_charts"] = sorted(tree_ids) == sorted(all_chart_ids)
    if auto_layout:
        expected_rows = [expected_ids[index : index + 2] for index in range(0, len(expected_ids), 2)]
        checks["layout_auto_rows"] = rows == expected_rows
    return checks, rows, all_chart_ids


def _validate_dashboard(
    client: SupersetClient,
    expectation: dict[str, Any],
    calls: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any], dict[str, list[int]]]:
    call = _last_successful_call(calls, "generate_dashboard")
    payload = _tool_payload(call) if call else {}
    dashboard_id = int(payload["dashboard_id"]) if payload.get("dashboard_id") else None
    if dashboard_id is None:
        return False, {"reason": "no_dashboard_id"}, {"chart": [], "dashboard": []}
    try:
        detail = client.get_asset("dashboard", dashboard_id)
    except Exception as error:
        return False, {
            "reason": "dashboard_not_retrievable",
            "error": str(error),
            "dashboard": {"id": dashboard_id},
        }, {"chart": [], "dashboard": [dashboard_id]}
    position, position_json_ok = _json_object(detail.get("position_json"))
    chart_ids = sorted(
        {
            int(item["meta"]["chartId"])
            for item in position.values()
            if isinstance(item, dict)
            and item.get("type") == "CHART"
            and (item.get("meta") or {}).get("chartId") is not None
        }
    )
    expected_ids = sorted(map(int, expectation.get("chart_ids", [])))
    if expectation.get("exact_chart_ids"):
        chart_check = chart_ids == expected_ids
    else:
        chart_check = set(expected_ids) <= set(chart_ids)
    metadata, metadata_json_ok = _json_object(detail.get("json_metadata"))
    metadata_checks = {
        key: metadata.get(key) == value
        for key, value in expectation.get("json_metadata", {}).items()
    }
    layout_checks, layout_rows, layout_chart_ids = _dashboard_layout_evidence(
        position, expected_ids, bool(expectation.get("auto_layout"))
    )
    checks = {
        "dashboard_retrievable": True,
        "position_json": position_json_ok,
        "chart_ids": chart_check,
        "published": bool(detail.get("published"))
        is bool(expectation.get("published", False)),
        "title": _title_matches(detail.get("dashboard_title"), expectation),
        "json_metadata": metadata_json_ok and all(metadata_checks.values()),
        **layout_checks,
    }
    snapshot = {
        "id": dashboard_id,
        "dashboard_title": detail.get("dashboard_title"),
        "published": detail.get("published"),
        "chart_ids": chart_ids,
        "layout_chart_ids": layout_chart_ids,
        "layout_rows": layout_rows,
        "json_metadata_checks": metadata_checks,
        "url": client.asset_url("dashboard", dashboard_id),
    }
    return all(checks.values()), {
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "dashboard": snapshot,
    }, {
        "chart": [],
        "dashboard": [dashboard_id],
    }


def validate_case(
    client: SupersetClient,
    case: dict[str, Any],
    turn: dict[str, Any],
    latency_seconds: float,
) -> dict[str, Any]:
    assistant = turn["assistant"]
    extra = dict(assistant.get("extra") or {})
    calls = [item for item in extra.get("tool_calls", []) if isinstance(item, dict)]
    answer = str(assistant.get("content") or "")
    successful = {
        str(call.get("name")) for call in calls if call.get("ok") is True
    }
    required = set(case.get("required_tools", []))
    forbidden = set(case.get("forbidden_successful_tools", []))
    preferred = set(case.get("preferred_tools", []))
    tool_contract = required <= successful and not (forbidden & successful)
    completion = (
        assistant.get("status") == "complete"
        and extra.get("outcome") == "success"
        and bool(answer.strip())
    )
    sql_score: bool | None = None
    answer_score: bool | None = None
    sql_evidence: dict[str, Any] | None = None
    if case.get("golden_sql"):
        sql_score, answer_score, sql_evidence = _validate_sql_case(
            client, case, calls, answer
        )
    elif case.get("answer_any"):
        answer_score = _answer_matches_any(answer, case["answer_any"])
    else:
        answer_score = bool(answer.strip())

    forbidden_answer_patterns = case.get("answer_must_not_match", [])
    forbidden_answer_pattern_hits = [
        pattern
        for pattern in forbidden_answer_patterns
        if re.search(pattern, _answer_text(answer), re.IGNORECASE | re.DOTALL)
    ]
    answer_consistency: bool | None = (
        not forbidden_answer_pattern_hits if forbidden_answer_patterns else None
    )

    asset_score: bool | None = None
    asset_evidence: dict[str, Any] | None = None
    generated_assets = {"chart": [], "dashboard": []}
    if expectation := case.get("asset"):
        if expectation["kind"] == "chart":
            asset_score, asset_evidence, generated_assets = _validate_chart(
                client, expectation, calls
            )
        else:
            asset_score, asset_evidence, generated_assets = _validate_dashboard(
                client, expectation, calls
            )

    safety_score: bool | None = None
    if case.get("safety_case"):
        safety_score = not (forbidden & successful) and bool(answer_score)

    dimensions: dict[str, bool | None] = {
        "completion": completion,
        "sql": sql_score,
        "answer": answer_score,
        "answer_consistency": answer_consistency,
        "tool_contract": tool_contract,
        "asset": asset_score,
        "safety": safety_score,
    }
    hard_gates = {
        name: value
        for name, value in dimensions.items()
        if value is not None and name != "sql"
    }
    failed_calls = [call for call in calls if call.get("ok") is not True]
    tool_success_rate = (
        round((len(calls) - len(failed_calls)) / len(calls), 4) if calls else 1.0
    )
    return {
        "case_id": case["id"],
        "category": case["category"],
        "profile": case["profile"],
        "prompt": case["prompt"],
        "page_context": case.get("page_context"),
        "passed": all(hard_gates.values()),
        "dimensions": dimensions,
        "hard_gates": hard_gates,
        "latency_seconds": round(latency_seconds, 3),
        "turns": int(extra.get("turns") or 0),
        "model": extra.get("model"),
        "answer": answer,
        "answer_evidence": {
            "forbidden_pattern_hits": forbidden_answer_pattern_hits,
        },
        "run_id": turn["run_id"],
        "thread_uuid": turn["thread_uuid"],
        "events": turn["events"],
        "stream_errors": turn["stream_errors"],
        "successful_tools": sorted(successful),
        "required_tools": sorted(required),
        "preferred_tools_used": sorted(preferred & successful),
        "forbidden_successful_tools": sorted(forbidden & successful),
        "failed_tool_calls": failed_calls,
        "tool_success_rate": tool_success_rate,
        "tool_calls": calls,
        "sql_evidence": sql_evidence,
        "asset_evidence": asset_evidence,
        "generated_assets": generated_assets,
    }


def load_benchmark(path: Path, selected: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = list(document.get("cases") or [])
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)) or any(not case_id for case_id in ids):
        raise ValueError("Every benchmark case must have a unique non-empty id")
    required = {"id", "category", "profile", "prompt"}
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"Case {case.get('id')} is missing {sorted(missing)}")
    if selected:
        unknown = selected - set(ids)
        if unknown:
            raise ValueError(f"Unknown case ids: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in selected]
    return document, cases


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [result for result in results if not result.get("harness_error")]
    passed = sum(bool(result.get("passed")) for result in evaluable)
    dimensions: dict[str, dict[str, Any]] = {}
    for name in (
        "completion",
        "sql",
        "answer",
        "answer_consistency",
        "tool_contract",
        "asset",
        "safety",
    ):
        values = [
            result["dimensions"][name]
            for result in evaluable
            if result["dimensions"].get(name) is not None
        ]
        if values:
            count = sum(bool(value) for value in values)
            dimensions[name] = {
                "passed": count,
                "eligible": len(values),
                "rate": round(count / len(values), 4),
            }
    latencies = sorted(float(result["latency_seconds"]) for result in evaluable)
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None
    tool_calls = [call for result in evaluable for call in result.get("tool_calls", [])]
    failed_tool_calls = sum(call.get("ok") is not True for call in tool_calls)
    categories: dict[str, dict[str, Any]] = {}
    for category in sorted({str(result.get("category")) for result in evaluable}):
        category_results = [
            result for result in evaluable if result.get("category") == category
        ]
        category_passed = sum(bool(result.get("passed")) for result in category_results)
        categories[category] = {
            "passed": category_passed,
            "eligible": len(category_results),
            "rate": round(category_passed / len(category_results), 4),
        }
    retained_urls = [
        snapshot["url"]
        for result in evaluable
        for snapshot in _asset_snapshots(result.get("asset_evidence"))
        if not snapshot.get("deleted")
    ]
    return {
        "total_cases": len(results),
        "evaluable_cases": len(evaluable),
        "passed_cases": passed,
        "business_ready_rate": round(passed / len(evaluable), 4) if evaluable else None,
        "dimensions": dimensions,
        "categories": categories,
        "median_latency_seconds": round(statistics.median(latencies), 3) if latencies else None,
        "p95_latency_seconds": round(p95, 3) if p95 is not None else None,
        "tool_calls": len(tool_calls),
        "failed_tool_calls": failed_tool_calls,
        "tool_success_rate": round(
            (len(tool_calls) - failed_tool_calls) / len(tool_calls), 4
        )
        if tool_calls
        else 1.0,
        "langfuse_synced_cases": sum(bool(result.get("langfuse")) for result in results),
        "harness_errors": sum(bool(result.get("harness_error")) for result in results),
        "telemetry_errors": sum(bool(result.get("telemetry_error")) for result in results),
        "cleanup_errors": sum(bool(result.get("cleanup_errors")) for result in results),
        "retained_asset_urls": retained_urls,
    }


def _asset_snapshots(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not evidence:
        return []
    snapshots = list(evidence.get("charts") or [])
    if isinstance(evidence.get("dashboard"), dict):
        snapshots.append(evidence["dashboard"])
    return snapshots


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Superset native AI benchmark",
        "",
        f"Run: `{report['run_name']}`  ",
        f"Benchmark: `{report['benchmark_version']}`  ",
        f"Cases passed: **{summary['passed_cases']}/{summary['evaluable_cases']}** "
        f"({summary['passed_cases'] / max(1, summary['evaluable_cases']) * 100:.1f}%)  ",
        f"Median / p95 latency: **{summary['median_latency_seconds']} / "
        f"{summary['p95_latency_seconds']} s**  ",
        f"Tool success: **{summary['tool_success_rate'] * 100:.1f}%** "
        f"({summary['tool_calls'] - summary['failed_tool_calls']}/{summary['tool_calls']})  ",
        f"Langfuse synced: **{summary['langfuse_synced_cases']}/{summary['total_cases']}**",
        "",
        "## Evaluation dimensions",
        "",
        "| Dimension | Passed | Eligible | Rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    if report.get("scorer_revision"):
        lines[4:4] = [
            f"Scorer revision: `{report['scorer_revision']}`  ",
            f"Source report: `{report.get('source_report', '-')}`  ",
        ]
    for name, item in summary["dimensions"].items():
        lines.append(
            f"| {name} | {item['passed']} | {item['eligible']} | "
            f"{item['passed'] / item['eligible'] * 100:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Category | Result | Latency | Turns | Trace |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for result in report["results"]:
        status = "ERROR" if result.get("harness_error") else ("PASS" if result["passed"] else "FAIL")
        trace = (result.get("langfuse") or {}).get("trace_id", result.get("run_id", "-"))
        lines.append(
            f"| {result['case_id']} | {result['category']} | {status} | "
            f"{result.get('latency_seconds', '-')} | {result.get('turns', '-')} | `{trace}` |"
        )
    assets = summary["retained_asset_urls"]
    if assets:
        lines.extend(["", "## Retained evidence", ""])
        lines.extend(f"- {url}" for url in assets)
    failures = [
        result
        for result in report["results"]
        if not result.get("passed") or result.get("harness_error") or result.get("telemetry_error")
    ]
    if failures:
        lines.extend(["", "## Failure diagnostics", ""])
        for result in failures:
            failed_gates = [
                name for name, value in result.get("hard_gates", {}).items() if not value
            ]
            detail = result.get("harness_error") or result.get("telemetry_error") or ", ".join(failed_gates)
            lines.append(f"- `{result['case_id']}`: {detail}")
    lines.extend(["", f"> {report.get('description', 'Live execution-grounded benchmark.')} ", ""])
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _cleanup_assets(
    client: SupersetClient, result: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    generated = result.get("generated_assets") or {}
    for kind in ("dashboard", "chart"):
        for asset_id in generated.get(kind, []):
            try:
                client.delete_asset(kind, int(asset_id))
            except Exception as error:  # Cleanup must not erase evaluation evidence.
                errors.append(f"{kind}:{asset_id}: {error}")
    for snapshot in _asset_snapshots(result.get("asset_evidence")):
        if int(snapshot.get("id") or -1) in {
            int(asset_id)
            for asset_ids in generated.values()
            for asset_id in asset_ids
        }:
            snapshot["deleted"] = not errors
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--goldens", type=Path)
    parser.add_argument(
        "--protect-database",
        metavar="SQL_SERVER_DATABASE",
        help="Wait for two idle SQL Server DMV polls before database-touching work.",
    )
    parser.add_argument("--load-gate-superset-database-id", type=int, default=3)
    parser.add_argument("--load-gate-poll-seconds", type=float, default=30)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--run-name")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument("--cleanup-assets", action="store_true")
    parser.add_argument("--keep-threads", action="store_true")
    args = parser.parse_args()
    if args.resume and not args.run_name:
        parser.error("--resume requires --run-name")
    if args.run_name and not re.fullmatch(r"[A-Za-z0-9._-]+", args.run_name):
        parser.error("--run-name may contain only letters, digits, dot, underscore, dash")
    if args.load_gate_poll_seconds < 0:
        parser.error("--load-gate-poll-seconds must be non-negative")
    return args


def main() -> int:
    args = parse_args()
    document, cases = load_benchmark(args.cases, set(args.case))
    goldens_sha256: str | None = None
    if args.goldens:
        goldens_document = json.loads(args.goldens.read_text(encoding="utf-8"))
        golden_rows = goldens_document.get("golden_rows") or {}
        expected_golden_ids = {case["id"] for case in cases if case.get("golden_sql")}
        if goldens_document.get("benchmark_version") != document["benchmark_version"]:
            raise RuntimeError("Frozen goldens use a different benchmark version")
        missing_golden_ids = expected_golden_ids - set(golden_rows)
        if missing_golden_ids:
            raise RuntimeError(
                "Frozen goldens are missing SQL cases: "
                f"{sorted(missing_golden_ids)}"
            )
        for case in cases:
            if case["id"] in golden_rows:
                rows = golden_rows[case["id"]]
                if not isinstance(rows, list) or len(rows) != 1:
                    raise RuntimeError(f"Frozen golden for {case['id']} must contain one row")
                case["_golden_rows"] = rows
        goldens_sha256 = hashlib.sha256(args.goldens.read_bytes()).hexdigest()
    run_name = args.run_name or (
        "superset-native-ai-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    partial_path = args.output_dir / f"{run_name}.partial.json"
    json_path = args.output_dir / f"{run_name}.json"
    markdown_path = args.output_dir / f"{run_name}.md"
    case_ids = [case["id"] for case in cases]
    cases_sha256 = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    langfuse_enabled = not args.no_langfuse and bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
    database_protection = (
        {
            "server_database_name": args.protect_database,
            "superset_database_id": args.load_gate_superset_database_id,
            "poll_seconds": args.load_gate_poll_seconds,
        }
        if args.protect_database
        else None
    )
    report: dict[str, Any] | None = None
    existing_path = partial_path if partial_path.exists() else json_path if json_path.exists() else None
    if existing_path and not args.resume:
        raise RuntimeError(f"Run {run_name} already exists; use --resume")
    if args.resume and existing_path:
        report = json.loads(existing_path.read_text(encoding="utf-8"))
        expected_identity = {
            "benchmark_version": document["benchmark_version"],
            "scorer_revision": SCORER_REVISION,
            "dataset_name": document["dataset_name"],
            "cases_sha256": cases_sha256,
            "case_ids": case_ids,
            "goldens_sha256": goldens_sha256,
            "database_protection": database_protection,
            "langfuse_enabled": langfuse_enabled,
        }
        if report.get("goldens_sha256") is None and goldens_sha256:
            result_by_id = {
                result.get("case_id"): result for result in report.get("results", [])
            }
            conflicts = [
                case["id"]
                for case in cases
                if case.get("_golden_rows") is not None
                and (result := result_by_id.get(case["id"]))
                and not result.get("harness_error")
                and (result.get("sql_evidence") or {}).get("golden_rows")
                != case["_golden_rows"]
            ]
            if conflicts:
                raise RuntimeError(f"Frozen goldens conflict with completed cases: {conflicts}")
            report["goldens_sha256"] = goldens_sha256
        if report.get("database_protection") is None and database_protection:
            report["database_protection"] = database_protection
        mismatches = {
            key: {"expected": value, "actual": report.get(key)}
            for key, value in expected_identity.items()
            if report.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"Cannot resume a different benchmark: {mismatches}")
        report.pop("finished_at", None)
    client = SupersetClient(
        os.environ.get("SUPERSET_BENCHMARK_URL", "http://superset:8088"),
        os.environ.get(
            "SUPERSET_BENCHMARK_PUBLIC_URL",
            os.environ.get("SUPERSET_BENCHMARK_URL", "http://superset:8088"),
        ),
        os.environ.get("SUPERSET_BENCHMARK_USERNAME", "admin"),
        os.environ["ADMIN_PASSWORD"],
    )
    langfuse: LangfuseBridge | None = None
    if langfuse_enabled:
        langfuse = LangfuseBridge(
            document["dataset_name"],
            run_name,
            "Dashboard-grounded evaluation of call-center questions and SQL answers.",
        )
    if report is None:
        report = {
            "run_name": run_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_version": document["benchmark_version"],
            "scorer_revision": SCORER_REVISION,
            "dataset_name": document["dataset_name"],
            "description": document.get("description", "Live execution-grounded benchmark."),
            "cases_sha256": cases_sha256,
            "goldens_sha256": goldens_sha256,
            "database_protection": database_protection,
            "case_ids": case_ids,
            "expected_cases": len(cases),
            "langfuse_enabled": langfuse_enabled,
            "superset_url": client.public_url,
            "results": [],
        }
    results_by_id = {
        result["case_id"]: result
        for result in report.get("results", [])
        if result.get("case_id") in case_ids
    }
    completed = {
        case_id
        for case_id, result in results_by_id.items()
        if not result.get("harness_error")
        and not result.get("telemetry_error")
        and (not langfuse_enabled or result.get("langfuse"))
    }
    if completed:
        print(f"Resuming {run_name}: keeping {len(completed)}/{len(cases)} completed cases", flush=True)
    load_gate = (
        DatabaseLoadGate(
            client,
            args.load_gate_superset_database_id,
            args.protect_database,
            args.load_gate_poll_seconds,
        )
        if args.protect_database
        else None
    )
    try:
        for index, case in enumerate(cases, 1):
            if case["id"] in completed:
                print(f"[{index}/{len(cases)}] {case['id']} SKIP", flush=True)
                continue
            print(f"[{index}/{len(cases)}] {case['id']} ({case['profile']})", flush=True)
            load_gate_wait = (
                load_gate.wait()
                if load_gate and case["category"] in {"business_answer", "chart"}
                else 0.0
            )
            thread_uuid: str | None = None
            item_id: str | None = None
            started = time.monotonic()
            try:
                if langfuse:
                    item_id = langfuse.prepare_item(
                        case, document["benchmark_version"]
                    )
                thread_uuid = client.create_thread(
                    f"[BENCH] {run_name} {case['id']}", case["profile"]
                )
                turn = client.run_turn(
                    thread_uuid,
                    case["profile"],
                    case["prompt"],
                    case.get("page_context"),
                )
                result = validate_case(
                    client, case, turn, time.monotonic() - started
                )
                if load_gate:
                    result["database_load_gate_wait_seconds"] = load_gate_wait
                if langfuse and item_id:
                    try:
                        result["langfuse"] = langfuse.sync_result(
                            item_id, result, document["benchmark_version"]
                        )
                    except Exception as error:  # Preserve local evaluation on telemetry failure.
                        result["telemetry_error"] = str(error)
                if args.cleanup_assets or not case.get("retain_assets", False):
                    if errors := _cleanup_assets(client, result):
                        result["cleanup_errors"] = errors
            except Exception as error:
                result = {
                    "case_id": case["id"],
                    "category": case["category"],
                    "profile": case["profile"],
                    "prompt": case["prompt"],
                    "page_context": case.get("page_context"),
                    "passed": False,
                    "harness_error": f"{type(error).__name__}: {error}",
                    "latency_seconds": round(time.monotonic() - started, 3),
                    "database_load_gate_wait_seconds": load_gate_wait,
                }
            finally:
                if thread_uuid and not args.keep_threads:
                    try:
                        client.delete_thread(thread_uuid)
                    except Exception as error:
                        result.setdefault("cleanup_errors", []).append(
                            f"thread:{thread_uuid}: {error}"
                        )
            results_by_id[case["id"]] = result
            report["results"] = [
                results_by_id[case_id] for case_id in case_ids if case_id in results_by_id
            ]
            report["summary"] = summarize(report["results"])
            _write_json(partial_path, report)
            print(
                f"  {'PASS' if result.get('passed') else 'FAIL'} "
                f"{result.get('hard_gates') or result.get('harness_error')}",
                flush=True,
            )
    finally:
        if langfuse:
            langfuse.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = summarize(report["results"])
    _write_json(json_path, report)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    _write_json(args.output_dir / "latest.json", report)
    (args.output_dir / "latest.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    partial_path.unlink(missing_ok=True)
    print(json_path)
    print(markdown_path)
    summary = report["summary"]
    return 2 if (
        summary["harness_errors"]
        or summary["telemetry_errors"]
        or summary["cleanup_errors"]
        or summary["total_cases"] != report["expected_cases"]
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
