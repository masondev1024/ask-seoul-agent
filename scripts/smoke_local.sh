#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18000}"
BASE_URL="http://${HOST}:${PORT}"
LOG_FILE="$(mktemp -t ask-seoul-agent-local-smoke.XXXXXX.log)"
PYTHON_BIN="${PYTHON:-python3}"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  rm -f "${LOG_FILE}"
}
trap cleanup EXIT

AGENT_PROVIDER=demo AGENT_MAX_CONCURRENT=1 PYTHONPATH=src uv run --locked python -m uvicorn ask_seoul_agent.main:app --host "${HOST}" --port "${PORT}" >"${LOG_FILE}" 2>&1 &
SERVER_PID="$!"

"${PYTHON_BIN}" - "${BASE_URL}" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1]

def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=3) as response:
        if response.status != 200:
            raise SystemExit(f"{path} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))

deadline = time.monotonic() + 20
while True:
    try:
        live = get_json("/health/live")
        ready = get_json("/health/ready")
        meta = get_json("/api/v1/meta")
        with urllib.request.urlopen(f"{base_url}/", timeout=3) as response:
            index = response.read().decode("utf-8")
        break
    except (ConnectionError, OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.25)

assert live["status"] == "ok", live
assert ready["status"] == "ready", ready
assert ready["provider_mode"] == "demo", ready
assert meta["provider_mode"] == "demo", meta
assert meta["ready"] is True, meta
assert "<!doctype html>" in index.lower(), "frontend index was not served at /"
print("local smoke ok")
PY
