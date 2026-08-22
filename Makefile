.PHONY: backend-lint backend-type backend-test eval-deterministic eval-live e2e-live-gemini frontend-test frontend-type frontend-build docker-build smoke-local smoke-container ci

EVAL_MANIFEST ?= evals/cases.v1.json
EVAL_RESULTS_DIR ?= evals/results
LIVE_PROVIDER ?= gemini
LIVE_DELAY_SECONDS ?= 75

backend-lint:
	uv run --locked ruff check .

backend-type:
	MYPYPATH=src uv run --locked mypy

backend-test:
	uv run --locked pytest

eval-deterministic:
	PYTHONPATH=src uv run --locked python -m ask_seoul_agent.eval_cli deterministic --manifest $(EVAL_MANIFEST) --output $(EVAL_RESULTS_DIR)/deterministic-latest.json

eval-live:
	PYTHONPATH=src uv run --env-file .env --locked python -m ask_seoul_agent.eval_cli live --provider $(LIVE_PROVIDER) --manifest $(EVAL_MANIFEST) --output $(EVAL_RESULTS_DIR)/live-latest.json --inter-case-delay-seconds $(LIVE_DELAY_SECONDS)

e2e-live-gemini:
	uv run --env-file .env --locked pytest -q -m live -k gemini_and_ask_seoul

frontend-test:
	npm --prefix frontend test

frontend-type:
	npm --prefix frontend run typecheck

frontend-build:
	npm --prefix frontend run build

docker-build:
	docker build -t ask-seoul-agent:local .

smoke-local:
	./scripts/smoke_local.sh

smoke-container:
	./scripts/smoke_container.sh

ci: backend-lint backend-type backend-test frontend-test frontend-type frontend-build docker-build
