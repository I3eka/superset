# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Langfuse sink for the native AI telemetry contract."""

from __future__ import annotations

from typing import Any

from langfuse import Langfuse, propagate_attributes

from superset.ai.telemetry import (
    AITelemetry,
    ModelCallTrace,
    RunTrace,
    ToolCallTrace,
)


class LangfuseAITelemetry(AITelemetry):
    """Send native AI run, model, and tool observations to Langfuse."""

    def __init__(self) -> None:
        self._client = Langfuse()
        self._runs: dict[str, Any] = {}

    def on_run_start(self, run: RunTrace) -> None:
        trace_id = self._client.create_trace_id(seed=run.run_id)
        with self._client.start_as_current_observation(
            trace_context={"trace_id": trace_id},
            name="superset.ai.run",
            as_type="agent",
            metadata=_run_metadata(run),
            end_on_exit=False,
        ) as observation:
            with propagate_attributes(
                user_id=str(run.user_id) if run.user_id is not None else None,
                session_id=run.thread_uuid,
                trace_name="superset.ai.run",
                tags=["superset", "native-ai"],
                metadata={"run_id": run.run_id},
            ):
                pass
        self._runs[run.run_id] = observation

    def on_model_call(self, run: RunTrace, call: ModelCallTrace) -> None:
        parent = self._runs.get(run.run_id)
        if parent is None:
            return
        usage = {
            key: value
            for key, value in (
                ("input", call.input_tokens),
                ("output", call.output_tokens),
            )
            if value is not None
        }
        observation = parent.start_observation(
            name="superset.ai.model_call",
            as_type="generation",
            input=call.system_prompt,
            output=call.response_text,
            model=call.model or run.model,
            usage_details=usage or None,
            metadata={
                "turn": call.turn,
                "duration_ms": call.duration_ms,
                "stop_reason": call.stop_reason,
                "error_type": call.error_type,
            },
            level="ERROR" if call.error_type else "DEFAULT",
            status_message=call.error_type,
        )
        observation.end()

    def on_tool_call(self, run: RunTrace, call: ToolCallTrace) -> None:
        parent = self._runs.get(run.run_id)
        if parent is None:
            return
        observation = parent.start_observation(
            name=f"superset.ai.tool.{call.tool_name}",
            as_type="tool",
            input=call.arguments,
            output=call.output,
            metadata={
                "duration_ms": call.duration_ms,
                "ok": call.ok,
                "error_type": call.error_type,
                "truncated": call.truncated,
            },
            level="ERROR" if not call.ok else "DEFAULT",
            status_message=call.error_type,
        )
        observation.end()

    def on_error(self, run: RunTrace, error: BaseException | str) -> None:
        if observation := self._runs.get(run.run_id):
            observation.update(level="ERROR", status_message=str(error)[:1_000])

    def on_run_end(self, run: RunTrace) -> None:
        observation = self._runs.pop(run.run_id, None)
        if observation is None:
            return
        try:
            observation.update(
                input=run.question,
                output=run.answer,
                metadata=_run_metadata(run),
                level=_run_level(run),
                status_message=run.error_type,
            )
        finally:
            observation.end()


def _run_metadata(run: RunTrace) -> dict[str, Any]:
    """Return stable Langfuse metadata for a run."""
    return {
        "run_id": run.run_id,
        "thread_uuid": run.thread_uuid,
        "user_id": run.user_id,
        "agent_key": run.agent_key,
        "model": run.model,
        "duration_ms": run.duration_ms,
        "outcome": run.outcome.value if run.outcome else None,
        "turns": run.turns,
        "usage": dict(run.usage) if run.usage else None,
        "error_type": run.error_type,
    }


def _run_level(run: RunTrace) -> str:
    """Map a run outcome to a Langfuse observation level."""
    if run.outcome is None or run.outcome.value == "success":
        return "DEFAULT"
    return "ERROR" if run.outcome.value == "error" else "WARNING"
