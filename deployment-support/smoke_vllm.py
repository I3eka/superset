from __future__ import annotations

import os

from openai import OpenAI


client = OpenAI(
    base_url=os.environ["AI_BASE_URL"],
    api_key=os.environ["AI_API_KEY"],
    max_retries=0,
    timeout=60,
)
model = os.environ["AI_MODEL"]

plain = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with exactly: native-ai-ok"}],
    max_tokens=128,
    temperature=0,
)
plain_text = plain.choices[0].message.content or ""
if "native-ai-ok" not in plain_text.lower():
    raise RuntimeError(f"Unexpected plain completion: {plain_text!r}")

stream = client.chat.completions.create(
    model=model,
    messages=[
        {
            "role": "user",
            "content": (
                "Call the smoke_echo tool with text native-ai-tool-ok. "
                "Do not answer directly."
            ),
        }
    ],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "smoke_echo",
                "description": "Echo text for a protocol smoke test.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }
    ],
    max_tokens=512,
    temperature=0,
    stream=True,
    stream_options={"include_usage": True},
)

tool_fragments: list[str] = []
finish_reason = None
usage_seen = False
for chunk in stream:
    if chunk.usage is not None:
        usage_seen = True
    if not chunk.choices:
        continue
    choice = chunk.choices[0]
    finish_reason = choice.finish_reason or finish_reason
    for call in choice.delta.tool_calls or []:
        if call.function and call.function.arguments:
            tool_fragments.append(call.function.arguments)

arguments = "".join(tool_fragments)
if "native-ai-tool-ok" not in arguments:
    raise RuntimeError(
        f"Tool call was not streamed: finish={finish_reason!r}, args={arguments!r}"
    )

print(
    "VLLM_SMOKE_OK",
    f"model={model}",
    f"finish={finish_reason}",
    f"usage={usage_seen}",
)
