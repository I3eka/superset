from __future__ import annotations

import os

from langfuse_telemetry import LangfuseAITelemetry


SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_SQLALCHEMY_DATABASE_URI"]
SQLALCHEMY_EXAMPLES_URI = os.environ["SUPERSET_EXAMPLES_URI"]

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_native_ai_",
    "CACHE_REDIS_URL": os.environ.get("REDIS_URL", "redis://redis:6379/1"),
}
DATA_CACHE_CONFIG = CACHE_CONFIG
FILTER_STATE_CACHE_CONFIG = CACHE_CONFIG
EXPLORE_FORM_DATA_CACHE_CONFIG = CACHE_CONFIG

# Both flags are required: ENABLE_EXTENSIONS renders the upstream core.chat
# host, while AI_ASSISTANT registers Superset's first-party assistant in it.
FEATURE_FLAGS = {
    "ENABLE_EXTENSIONS": True,
    "AI_ASSISTANT": True,
}

AI_LLM_PROVIDER_CLASS = (
    "superset.ai.llm.openai_compatible.OpenAICompatibleProvider"
)

_llm_base_url = os.environ.get("VLLM_BASE_URL") or os.environ.get("AI_BASE_URL")
_llm_api_key = os.environ.get("VLLM_API_KEY") or os.environ.get("AI_API_KEY")
_llm_model = (
    os.environ.get("VLLM_MODEL")
    or os.environ.get("AI_MODEL")
    or "Qwen/Qwen3.6-27B"
)

if not _llm_base_url or not _llm_api_key:
    raise RuntimeError("The native AI assistant requires a vLLM URL and API key")

AI_LLM_PROVIDER_CONFIG = {
    "base_url": _llm_base_url,
    "api_key": _llm_api_key,
    # Reserve room for a useful answer inside the 24,576-token vLLM context.
    "max_output_tokens": 2_048,
    "models": {
        "default": _llm_model,
        "fast": _llm_model,
        "reasoning": _llm_model,
    },
}

# Use Superset's default turn budget for both assistant profiles.
AI_AGENT_MAX_TURNS = 20
AI_AGENT_PROFILES = {
    "analyst": {"max_turns": 20},
    "builder": {
        "name": "Dashboard builder",
        "description": "Creates native Superset charts and dashboards.",
        "tools": [
            "search_assets",
            "list_databases",
            "get_schema",
            "execute_sql",
            "validate_sql",
            "get_chart_context",
            "get_dashboard_context",
            "create_virtual_dataset",
            "generate_chart",
            "generate_dashboard",
        ],
        "model_alias": "reasoning",
        "max_turns": 24,
        "required_permission": ("can_write", "Dashboard"),
    },
}
AI_AGENT_MAX_RESULT_ROWS = 100
AI_AGENT_MAX_RESULT_BYTES = 8 * 1024
AI_ASSISTANT_MAX_HISTORY_MESSAGES = 12
AI_ASSISTANT_MAX_HISTORY_CHARS = 40_000

# This deployment uses an internal, self-hosted Langfuse instance. Preserve
# prompt, answer, SQL, and tool content so traces can diagnose answer quality.
AI_TELEMETRY = [LangfuseAITelemetry()]
AI_TELEMETRY_REDACT_CONTENT = False

# Inline mode is the upstream default and does not require a Celery worker.
AI_ASSISTANT_EXECUTION_MODE = "inline"

SUPERSET_WEBSERVER_ADDRESS = "http://superset:8088"
WEBDRIVER_BASEURL = "http://superset:8088/"
WEBDRIVER_BASEURL_USER_FRIENDLY = "http://10.48.140.17:8088/"

TALISMAN_ENABLED = False
WTF_CSRF_ENABLED = True
ROW_LIMIT = 5_000
