# syntax=docker/dockerfile:1.7

FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt --output-file /tmp/requirements.txt >/dev/null \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv build --wheel --out-dir /tmp/dist \
    && uv pip install --system --no-cache --no-deps /tmp/dist/*.whl

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_PROVIDER=demo \
    AGENT_MAX_CONCURRENT=2 \
    LOG_LEVEL=INFO

WORKDIR /app

COPY --from=python-build /usr/local /usr/local
COPY --from=frontend-build /build/frontend/dist /tmp/frontend-dist

RUN set -eux; \
    static_dir="$(python -c "from pathlib import Path; import ask_seoul_agent; print(Path(ask_seoul_agent.__file__).resolve().parent / 'static')")"; \
    mkdir -p "$static_dir"; \
    cp -a /tmp/frontend-dist/. "$static_dir/"; \
    rm -rf /tmp/frontend-dist; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app; \
    chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "ask_seoul_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
