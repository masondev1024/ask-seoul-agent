#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE:-ask-seoul-agent}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
PYTHON_BIN="${PYTHON:-python3}"
SMOKE_PROVIDER="${SMOKE_PROVIDER:-demo}"

case "${SMOKE_PROVIDER}" in
  demo|gemini|anthropic) ;;
  *)
    echo "unsupported SMOKE_PROVIDER: ${SMOKE_PROVIDER}" >&2
    exit 2
    ;;
esac

cleanup() {
  docker compose down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

AGENT_PROVIDER="${SMOKE_PROVIDER}" docker compose up --build --detach "${SERVICE}"

"${PYTHON_BIN}" - "${BASE_URL}" "${SMOKE_PROVIDER}" <<'PY'
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1]
expected_provider = sys.argv[2]

def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=3) as response:
        if response.status != 200:
            raise SystemExit(f"{path} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))

deadline = time.monotonic() + 60
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
            subprocess.run(["docker", "compose", "ps"], check=False)
            subprocess.run(["docker", "compose", "logs", "--no-color"], check=False)
            raise
        time.sleep(0.5)

assert live["status"] == "ok", live
assert ready["status"] == "ready", ready
assert ready["provider_mode"] == expected_provider, ready
assert meta["provider_mode"] == expected_provider, meta
assert meta["ready"] is True, meta
assert "<!doctype html>" in index.lower(), "frontend index was not served at /"
print("container smoke ok")
PY
