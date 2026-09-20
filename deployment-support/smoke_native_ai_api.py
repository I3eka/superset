from __future__ import annotations

import base64
import json
import os
import time
import uuid
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, Request


base_url = os.environ.get("SUPERSET_SMOKE_URL", "http://superset:8088").rstrip("/")
opener = build_opener(HTTPCookieProcessor(CookieJar()))
agent_key = os.environ.get("AI_SMOKE_AGENT_KEY")
thread_title = os.environ.get("AI_SMOKE_THREAD_TITLE", "Native AI validation")


def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    csrf: str | None = None,
    payload: dict[str, object] | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, object]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if csrf:
        headers["X-CSRFToken"] = csrf
    request = Request(base_url + path, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {error.code}: {detail}") from error


def wait_for_langfuse_trace(run_id: str) -> tuple[str, int]:
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return "-", 0

    from langfuse import Langfuse

    client = Langfuse()
    trace_id = client.create_trace_id(seed=run_id)
    expected_environment = os.environ.get("LANGFUSE_TRACING_ENVIRONMENT")
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    last_names: set[str] = set()
    try:
        while time.monotonic() < deadline:
            try:
                trace = client.api.trace.get(trace_id)
                last_names = {observation.name for observation in trace.observations}
                if (
                    {"superset.ai.run", "superset.ai.model_call"} <= last_names
                    and trace.input is not None
                    and trace.output is not None
                    and trace.environment == expected_environment
                ):
                    return trace_id, len(trace.observations)
            except Exception as ex:  # noqa: BLE001
                last_error = ex
            time.sleep(1)
    finally:
        client.shutdown()

    raise RuntimeError(
        f"Langfuse trace {trace_id} was not complete after 30s; "
        f"observations={sorted(last_names)}, last_error={last_error}"
    )


status, login = call(
    "POST",
    "/api/v1/security/login",
    payload={
        "username": "admin",
        "password": os.environ["ADMIN_PASSWORD"],
        "provider": "db",
        "refresh": True,
    },
)
assert status == 200
token = str(login["access_token"])

status, csrf_response = call("GET", "/api/v1/security/csrf_token/", token=token)
assert status == 200
csrf = str(csrf_response["result"])

status, models = call("GET", "/api/v1/ai/model/", token=token)
assert status == 200
model_names = list(models["result"])
assert os.environ["AI_MODEL"] in model_names

status, agents = call("GET", "/api/v1/ai/agent/", token=token)
assert status == 200
agent_profiles = list(agents["result"])
assert agent_profiles

thread_payload: dict[str, object] = {"title": thread_title}
if agent_key:
    thread_payload["agent_key"] = agent_key

status, created = call(
    "POST",
    "/api/v1/ai/thread/",
    token=token,
    csrf=csrf,
    payload=thread_payload,
)
assert status == 201
thread_uuid = str(created["result"]["uuid"])

encoded_question = os.environ.get("AI_SMOKE_QUESTION_B64")
question = (
    base64.b64decode(encoded_question).decode("utf-8")
    if encoded_question
    else os.environ.get("AI_SMOKE_QUESTION")
    or (
        "Use the available Superset tools to count all rows in the "
        "birth_names example table. Give the exact count and briefly "
        "state how you obtained it."
    )
)

message_payload: dict[str, object] = {
    "content": question,
    "request_id": f"native-ai-smoke-{uuid.uuid4()}",
}
if agent_key:
    message_payload["agent_key"] = agent_key

status, accepted = call(
    "POST",
    f"/api/v1/ai/thread/{thread_uuid}/message",
    token=token,
    csrf=csrf,
    payload=message_payload,
)
assert status == 202
run_id = str(accepted["result"]["run_id"])

stream_path = f"/api/v1/ai/thread/{thread_uuid}/stream?" + urlencode({"run_id": run_id})
stream_request = Request(
    base_url + stream_path,
    headers={"Authorization": f"Bearer {token}"},
    method="GET",
)
events: list[str] = []
done_payload: dict[str, object] | None = None
try:
    with opener.open(stream_request, timeout=360) as response:
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
                data = "\n".join(data_lines)
                if event_name == "error":
                    raise RuntimeError(f"AI stream error: {data}")
                if event_name == "done":
                    done_payload = json.loads(data)
                event_name = "message"
                data_lines = []
except HTTPError as error:
    detail = error.read().decode("utf-8", errors="replace")
    raise RuntimeError(f"GET {stream_path} -> {error.code}: {detail}") from error

assert done_payload is not None
assert done_payload.get("ok") is True, done_payload

status, thread = call(
    "GET", f"/api/v1/ai/thread/{thread_uuid}", token=token, timeout=60
)
assert status == 200
messages = list(thread["result"]["messages"])
assistant = messages[-1]
assert assistant["role"] == "assistant"
assert assistant["status"] == "complete"
answer = str(assistant["content"])
assert answer.strip()
extra = dict(assistant.get("extra") or {})
assert extra.get("run_id") == run_id, extra
assert extra.get("outcome") == "success", extra
turns = int(extra.get("turns") or 0)
max_turns = int(os.environ.get("AI_SMOKE_MAX_TURNS", "10"))
assert 0 < turns <= max_turns, extra
tool_calls = list(extra.get("tool_calls") or [])
if agent_key:
    assert extra.get("agent_key") == agent_key, extra

expected_tools = {
    name.strip()
    for name in os.environ.get("AI_SMOKE_EXPECT_TOOLS", "").split(",")
    if name.strip()
}
successful_tools = {
    str(item.get("name"))
    for item in tool_calls
    if isinstance(item, dict) and item.get("ok") is True
}
assert expected_tools <= successful_tools, (expected_tools, tool_calls)

langfuse_trace_id, langfuse_observations = wait_for_langfuse_trace(run_id)

expected_chart_count = int(os.environ.get("AI_SMOKE_EXPECT_CHARTS", "0"))
successful_charts = [
    item
    for item in tool_calls
    if isinstance(item, dict)
    and item.get("name") == "generate_chart"
    and item.get("ok") is True
]
assert len(successful_charts) >= expected_chart_count, tool_calls

dashboard_id: int | None = None
if "generate_dashboard" in expected_tools:
    for item in tool_calls:
        if (
            isinstance(item, dict)
            and item.get("name") == "generate_dashboard"
            and item.get("ok") is True
        ):
            output = json.loads(str(item.get("output") or "{}"))
            if output.get("dashboard_id"):
                dashboard_id = int(output["dashboard_id"])
                break
    assert dashboard_id is not None, tool_calls
    dashboard_status, _ = call("GET", f"/api/v1/dashboard/{dashboard_id}", token=token)
    assert dashboard_status == 200

status, thread_list = call("GET", "/api/v1/ai/thread/", token=token)
assert status == 200
cleaned = 0
for candidate in list(thread_list["result"]):
    candidate_uuid = str(candidate["uuid"])
    if candidate.get("title") == thread_title and candidate_uuid != thread_uuid:
        deleted_status, _ = call(
            "DELETE",
            f"/api/v1/ai/thread/{candidate_uuid}",
            token=token,
            csrf=csrf,
        )
        assert deleted_status == 200
        cleaned += 1

print(
    "NATIVE_AI_API_OK",
    f"models={len(model_names)}",
    f"agents={len(agent_profiles)}",
    f"events={','.join(dict.fromkeys(events))}",
    f"run={run_id}",
    f"outcome={extra['outcome']}",
    f"turns={turns}",
    f"tools={len(tool_calls)}",
    f"tool_names={','.join(sorted(successful_tools))}",
    f"dashboard={dashboard_id or '-'}",
    f"thread={thread_uuid}",
    f"langfuse_trace={langfuse_trace_id}",
    f"langfuse_observations={langfuse_observations}",
    f"cleaned={cleaned}",
)
print("ANSWER", answer[:500].replace("\n", " "))
