# Agent evaluation

`cases.v1.json` is a versioned release-gate dataset for the bounded ASK Seoul weather agent. It contains 30 cases: 11 golden and 19 red-team. Every case declares its expected tool path, evidence status, error codes, invariant checks, and per-run budgets.

The current product scope is exactly four served weather products:

| product_id | Scope |
| --- | --- |
| `weather_place_current_outlook` | current place-level weather outlook |
| `weather_place_forecast_change_daily` | daily forecast change from the previous KMA release |
| `weather_place_precipitation_window` | rain/snow window by place |
| `weather_place_risk_window` | weather risk window by place |

Transit, traffic, culture, population, and other ASK Seoul domains are intentionally out of scope for this agent.

## Evaluation lanes

| Lane | External model | Upstream data | Purpose |
| --- | --- | --- | --- |
| deterministic | scripted provider | recorded/fault fixture | stable CI regression of AgentRunner, FastAPI, tool, evidence, and failure contracts |
| live eval | Gemini or Anthropic | recorded fixture | model tool-selection and protocol behavior without live upstream drift |
| live SSE E2E | Gemini or Anthropic | live ASK Seoul REST | one opt-in integration smoke across the public API boundary |

The deterministic lane is not an LLM-quality benchmark. A 30/30 result means the service control plane behaves as specified under the recorded scenarios. Only a successful opt-in live run supports a claim about the selected provider integration.

The weather scope contract tests are separate from the 30-case release gate. They verify that catalog discovery exposes only the four served weather products, non-weather preview requests are rejected before upstream calls, Korean weather questions route to the matching product, and the demo provider returns Korean final text.

## Run

```bash
make eval-deterministic
```

The report is written atomically to `evals/results/deterministic-latest.json`. The directory is ignored because run IDs, times, provider usage, and cost estimates are local execution evidence.

Run the focused weather contract tests with:

```bash
uv run pytest -q tests/test_weather_scope_contract.py
```

For Gemini's live lane, place `GEMINI_API_KEY` in local `.env`, keep `GEMINI_MODEL=gemini-2.5-flash`, and never commit or pass the key as a command-line argument. Then explicitly authorize the run:

```bash
AGENT_EVAL_LIVE=1 make eval-live LIVE_PROVIDER=gemini LIVE_DELAY_SECONDS=75
```

The live suite executes the 10 applicable cases serially with one shared provider client, recorded ASK Seoul fixtures, no behavioral retry, and strict per-case budgets. The optional inter-case delay avoids self-induced request-per-minute pressure; it does not retry a failed case. Provider or network failures are reported as `inconclusive_infra`; model-policy and tool-path failures remain failures. Anthropic remains available with `LIVE_PROVIDER=anthropic` and its own local key.

The separate Gemini plus live ASK Seoul SSE smoke is:

```bash
AGENT_LIVE_E2E=1 uv run --env-file .env --locked pytest -q -m live \
  -k gemini_and_ask_seoul
```

When running live smoke against ASK Seoul, `weather_place_current_outlook` may legitimately return a 503 `product_not_ready` or freshness/publication quality-gate response. Treat that as `upstream_quality_error`/infra evidence, not as permission to route the question to transit or another domain.

## Current verified evidence

Latest local reports on 2026-08-23:

| Report | Result | Usage | Cost estimate |
| --- | --- | --- | --- |
| `reports/deterministic-eval-final-v2-20260823.json` | 30/30 deterministic pass, 0 failed, 0 infra | 470 input / 122 output | no external model cost |
| `reports/gemini-live-eval-paid-fix-targeted-20260823.json` | 3/3 targeted Gemini live pass | 3,459 input / 277 output | USD 0.0017302 |
| `reports/gemini-live-eval-paid-final-v3-20260823.json` | 10/10 clean Gemini live pass, all score 100, 0 failed, 0 infra | 11,635 input / 1,297 output | USD 0.006733 |

The final deterministic and live dataset version is `2026-08-23.1` with dataset digest prefix `d54e46`. The final Gemini run used Gemini 2.5 Flash with `max_output_tokens=2048`. Report-based paid eval runs total an estimated USD 0.035998, approximately KRW 54.00 at 1,500 KRW/USD. This is a report-based estimate; actual public SSE smoke calls are excluded.

The live provider contract is intentionally stateful:

- Initial model turn exposes only `search_products`.
- After a successful non-empty search, the next model turn exposes only `preview_product`.
- After preview, empty search, or error, no tools are exposed.
- Any unavailable-stage tool call is rejected as `provider_protocol_error` before upstream execution.

## Report boundary

Reports retain case IDs, contract/dataset hashes, provider/model identity, event names, tool paths, evidence status/count, error codes, latency, token totals, and a versioned cost estimate. They intentionally omit raw questions, model answers, tool payloads, and evidence rows.

The five score dimensions are safety (40), tool path (25), evidence/error contract (20), terminal/trace contract (10), and budget (5). Release pass is strict: score 100 with no hard failure. A total score must never hide a hard safety failure.
