#!/usr/bin/env python3
"""Aggregate three complete Superset native AI release benchmark runs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _rate(items: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(bool(item.get("passed")) for item in items)
    return {
        "passed": passed,
        "eligible": len(items),
        "rate": round(passed / len(items), 4) if items else None,
    }


def _failure(result: dict[str, Any], run_name: str) -> dict[str, Any]:
    sql = result.get("sql_evidence") or {}
    asset = result.get("asset_evidence") or {}
    return {
        "run_name": run_name,
        "case_id": result["case_id"],
        "category": result.get("category"),
        "failed_gates": [
            name for name, value in result.get("hard_gates", {}).items() if not value
        ],
        "harness_error": result.get("harness_error"),
        "telemetry_error": result.get("telemetry_error"),
        "cleanup_errors": result.get("cleanup_errors", []),
        "trace_id": (result.get("langfuse") or {}).get("trace_id"),
        "run_id": result.get("run_id"),
        "expected_facts": sql.get("facts", []),
        "answer_evidence": result.get("answer_evidence"),
        "agent_sql": sql.get("agent_sql"),
        "asset_failed_checks": asset.get("failed_checks", []),
        "failed_tool_calls": result.get("failed_tool_calls", []),
        "answer": result.get("answer"),
    }


def aggregate_reports(
    reports: list[dict[str, Any]], expected_repeats: int = 3
) -> dict[str, Any]:
    if len(reports) != expected_repeats:
        raise ValueError(f"Expected {expected_repeats} reports, got {len(reports)}")
    baseline = reports[0]
    case_ids = list(baseline.get("case_ids") or [])
    identity_keys = (
        "benchmark_version",
        "scorer_revision",
        "dataset_name",
        "cases_sha256",
        "goldens_sha256",
        "database_protection",
        "case_ids",
    )
    all_results: list[dict[str, Any]] = []
    per_case: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for report in reports:
        mismatches = {
            key: {"expected": baseline.get(key), "actual": report.get(key)}
            for key in identity_keys
            if report.get(key) != baseline.get(key)
        }
        if mismatches:
            raise ValueError(f"Benchmark identity mismatch in {report.get('run_name')}: {mismatches}")
        results = list(report.get("results") or [])
        result_ids = [result.get("case_id") for result in results]
        if result_ids != case_ids or len(result_ids) != len(set(result_ids)):
            raise ValueError(f"Incomplete or reordered run: {report.get('run_name')}")
        for result in results:
            all_results.append(result)
            per_case[result["case_id"]].append((report["run_name"], result))

    latencies = sorted(float(result["latency_seconds"]) for result in all_results)
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)]
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
            {"passed": result["dimensions"][name]}
            for result in all_results
            if not result.get("harness_error")
            and result.get("dimensions", {}).get(name) is not None
        ]
        if values:
            dimensions[name] = _rate(values)
    categories = {
        category: _rate(
            [result for result in all_results if result.get("category") == category]
        )
        for category in sorted({str(result.get("category")) for result in all_results})
    }
    tool_calls = [call for result in all_results for call in result.get("tool_calls", [])]
    failed_tool_calls = sum(call.get("ok") is not True for call in tool_calls)
    case_stability = [
        {
            "case_id": case_id,
            "category": runs[0][1].get("category"),
            "passed_repeats": sum(bool(result.get("passed")) for _, result in runs),
            "expected_repeats": expected_repeats,
            "runs": [
                {
                    "run_name": run_name,
                    "passed": bool(result.get("passed")),
                    "trace_id": (result.get("langfuse") or {}).get("trace_id"),
                }
                for run_name, result in runs
            ],
        }
        for case_id, runs in per_case.items()
    ]
    failures = [
        _failure(result, report["run_name"])
        for report in reports
        for result in report["results"]
        if not result.get("passed")
        or result.get("harness_error")
        or result.get("telemetry_error")
        or result.get("cleanup_errors")
    ]
    overall = _rate(all_results)
    golden_sources = Counter(
        (result.get("sql_evidence") or {}).get("golden_source") or "missing"
        for result in all_results
        if result.get("sql_evidence")
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_version": baseline["benchmark_version"],
        "scorer_revision": baseline.get("scorer_revision"),
        "dataset_name": baseline["dataset_name"],
        "cases_sha256": baseline["cases_sha256"],
        "goldens_sha256": baseline.get("goldens_sha256"),
        "database_protection": baseline.get("database_protection"),
        "run_names": [report["run_name"] for report in reports],
        "expected_repeats": expected_repeats,
        "unique_cases": len(case_ids),
        "total_case_runs": len(all_results),
        "summary": {
            **overall,
            "stable_cases_all_repeats": sum(
                item["passed_repeats"] == expected_repeats for item in case_stability
            ),
            "categories": categories,
            "dimensions": dimensions,
            "median_latency_seconds": round(statistics.median(latencies), 3),
            "p95_latency_seconds": round(p95, 3),
            "tool_calls": len(tool_calls),
            "failed_tool_calls": failed_tool_calls,
            "tool_success_rate": round(
                (len(tool_calls) - failed_tool_calls) / len(tool_calls), 4
            )
            if tool_calls
            else 1.0,
            "langfuse_synced": sum(bool(result.get("langfuse")) for result in all_results),
            "unique_trace_ids": len(
                {
                    (result.get("langfuse") or {}).get("trace_id")
                    for result in all_results
                    if (result.get("langfuse") or {}).get("trace_id")
                }
            ),
            "harness_errors": sum(bool(result.get("harness_error")) for result in all_results),
            "telemetry_errors": sum(bool(result.get("telemetry_error")) for result in all_results),
            "cleanup_errors": sum(bool(result.get("cleanup_errors")) for result in all_results),
            "golden_sources": dict(sorted(golden_sources.items())),
            "database_load_gate_wait_seconds": round(
                sum(
                    float(result.get("database_load_gate_wait_seconds") or 0)
                    for result in all_results
                ),
                3,
            ),
        },
        "case_stability": case_stability,
        "failures": failures,
    }


def render_markdown(aggregate: dict[str, Any]) -> str:
    summary = aggregate["summary"]
    lines = [
        "# Superset native AI release benchmark",
        "",
        f"Runs: **{aggregate['expected_repeats']}**  ",
        f"Unique cases / case-runs: **{aggregate['unique_cases']} / {aggregate['total_case_runs']}**  ",
        f"Passed: **{summary['passed']}/{summary['eligible']} "
        f"({summary['passed'] / max(1, summary['eligible']) * 100:.1f}%)**  ",
        f"Stable cases ({aggregate['expected_repeats']}/{aggregate['expected_repeats']}): "
        f"**{summary['stable_cases_all_repeats']}/{aggregate['unique_cases']}**  ",
        f"Langfuse / unique traces: **{summary['langfuse_synced']} / {summary['unique_trace_ids']}**  ",
        f"Median / p95 latency: **{summary['median_latency_seconds']} / {summary['p95_latency_seconds']} s**",
        "",
        "## Categories",
        "",
        "| Category | Passed | Eligible | Rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in summary["categories"].items():
        lines.append(
            f"| {name} | {item['passed']} | {item['eligible']} | "
            f"{item['passed'] / item['eligible'] * 100:.1f}% |"
        )
    lines.extend(["", "## Evaluation dimensions", "", "| Dimension | Passed | Eligible | Rate |", "| --- | ---: | ---: | ---: |"])
    for name, item in summary["dimensions"].items():
        lines.append(
            f"| {name} | {item['passed']} | {item['eligible']} | "
            f"{item['passed'] / item['eligible'] * 100:.1f}% |"
        )
    lines.extend(["", "## Failures", ""])
    if not aggregate["failures"]:
        lines.append("No failures.")
    else:
        lines.extend(["| Run | Case | Gates / error | Trace |", "| --- | --- | --- | --- |"])
        for failure in aggregate["failures"]:
            detail = failure["harness_error"] or failure["telemetry_error"] or ", ".join(
                [*failure["failed_gates"], *failure["asset_failed_checks"]]
            )
            lines.append(
                f"| {failure['run_name']} | {failure['case_id']} | {detail} | "
                f"`{failure['trace_id'] or '-'}` |"
            )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-repeats", type=int, default=3)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    aggregate = aggregate_reports(reports, args.expected_repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(args.output, aggregate)
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(render_markdown(aggregate), encoding="utf-8")
    print(args.output)
    print(markdown_path)


if __name__ == "__main__":
    main()
