# syntax=docker/dockerfile:1.7
FROM python:3.11.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    HF_HOME=/cache/huggingface \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/workspace

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /cache/huggingface /cache/checkpoints \
    && chown -R app:app /cache

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

FROM base AS runtime
RUN uv sync --frozen --group inference --group rag --no-dev --no-install-project \
    && chown -R app:app /tmp/uv-cache /opt/venv
COPY --chown=app:app apps ./apps
COPY --chown=app:app packages ./packages
COPY --chown=app:app services ./services
COPY --chown=app:app configs ./configs
USER app

FROM base AS intent-runtime
RUN uv sync --frozen --group inference --no-dev --no-install-project \
    && chown -R app:app /tmp/uv-cache /opt/venv
COPY --chown=app:app apps ./apps
COPY --chown=app:app packages ./packages
COPY --chown=app:app services ./services
COPY --chown=app:app configs ./configs
USER app

FROM base AS dev
RUN uv sync --frozen --group dev --group data --group inference --group rag --no-install-project \
    && chown -R app:app /tmp/uv-cache /opt/venv
COPY --chown=app:app . .
USER app
CMD ["bash"]

FROM base AS trainer
RUN uv sync --frozen --group dev --group data --group ml --no-install-project \
    && chown -R app:app /tmp/uv-cache /opt/venv
COPY --chown=app:app . .
USER app
CMD ["python", "-m", "apps.intent_service.training.train"]
