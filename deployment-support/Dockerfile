FROM local/superset-native-ai:pr-42805

LABEL org.opencontainers.image.source="https://github.com/apache/superset/pull/42805" \
      org.opencontainers.image.revision="0e4a15cca1c5be2b7e68d609a9062f5e51193024" \
      org.opencontainers.image.title="Apache Superset native AI dashboard builder"

USER root

COPY --chown=superset:superset \
    superset/ai/factories.py \
    /app/superset/ai/factories.py

COPY --chown=superset:superset \
    superset/ai/tools/__init__.py \
    superset/ai/tools/authoring.py \
    superset/ai/tools/base.py \
    superset/ai/tools/search.py \
    /app/superset/ai/tools/

COPY --chown=superset:superset \
    superset/ai/policy.py \
    /app/superset/ai/

COPY --chown=superset:superset \
    superset/ai/runtime/messages.py \
    /app/superset/ai/runtime/

COPY --chown=superset:superset \
    superset/ai/prompts/core/persona.py \
    superset/ai/prompts/core/sql_conventions.py \
    /app/superset/ai/prompts/core/

COPY --chown=superset:superset \
    superset/sql/parse.py \
    /app/superset/sql/

COPY --chown=superset:superset \
    superset/models/helpers.py \
    /app/superset/models/

COPY --chown=superset:superset \
    superset/mcp_service/chart/chart_utils.py \
    superset/mcp_service/chart/compile.py \
    superset/mcp_service/chart/schemas.py \
    /app/superset/mcp_service/chart/

COPY --chown=superset:superset \
    superset/mcp_service/chart/tool/generate_chart.py \
    /app/superset/mcp_service/chart/tool/

COPY --chown=superset:superset \
    superset/mcp_service/chart/validation/dataset_validator.py \
    /app/superset/mcp_service/chart/validation/

COPY --chown=superset:superset \
    superset/mcp_service/dataset/schemas.py \
    /app/superset/mcp_service/dataset/

COPY --chown=superset:superset \
    superset/mcp_service/dataset/tool/create_virtual_dataset.py \
    /app/superset/mcp_service/dataset/tool/

# Provider, MCP runtime and database drivers used by this deployment.
RUN uv pip install --no-cache-dir \
    "fastmcp>=3.4.5,<4.0" \
    "langfuse==4.14.4" \
    "openai>=1.60.0,<2" \
    "psycopg2-binary==2.9.9" \
    "pymssql==2.3.13" \
    "tiktoken>=0.13.0,<1.0"

USER superset
