# Agent evaluation

`cases.v1.json` is a versioned release-gate dataset for the bounded ASK Seoul agent. It contains 30 cases: 11 golden and 19 red-team. Every case declares its expected tool path, evidence status, error codes, invariant checks, and per-run budgets.

## Evaluation lanes

| Lane | External model | Upstream data | Purpose |
| --- | --- | --- | --- |
| deterministic | scripted provider | recorded/fault fixture | stable CI regression of AgentRunner, FastAPI, tool, evidence, and failure contracts |
| live eval | Gemini or Anthropic | recorded fixture | model tool-selection and protocol behavior without live upstream drift |
| live SSE E2E | Gemini or Anthropic | live ASK Seoul REST | one opt-in integration smoke across the public API boundary |

The deterministic lane is not an LLM-quality benchmark. A 30/30 result means the service control plane behaves as specified under the recorded scenarios. Only a successful opt-in live run supports a claim about the selected provider integration.

## Run

```bash
make eval-deterministic
```

The report is written atomically to `evals/results/deterministic-latest.json`. The directory is ignored because run IDs, times, provider usage, and cost estimates are local execution evidence.

For Gemini's free-tier lane, place `GEMINI_API_KEY` in local `.env`, keep `GEMINI_MODEL=gemini-2.5-flash`, and never commit or pass the key as a command-line argument. Then explicitly authorize the run:

```bash
AGENT_EVAL_LIVE=1 make eval-live LIVE_PROVIDER=gemini LIVE_DELAY_SECONDS=75
```

The live suite executes the 10 applicable cases serially with one shared provider client, recorded ASK Seoul fixtures, no behavioral retry, and strict per-case budgets. The explicit inter-case delay avoids self-induced request-per-minute pressure on the free tier; it does not retry a failed case. Provider or network failures are reported as `inconclusive_infra`; model-policy and tool-path failures remain failures. Anthropic remains available with `LIVE_PROVIDER=anthropic` and its own local key.

The separate Gemini plus live ASK Seoul SSE smoke is:

```bash
AGENT_LIVE_E2E=1 uv run --env-file .env --locked pytest -q -m live \
  -k gemini_and_ask_seoul
```

## Report boundary

Reports retain case IDs, contract/dataset hashes, provider/model identity, event names, tool paths, evidence status/count, error codes, latency, token totals, and a versioned cost estimate. They intentionally omit raw questions, model answers, tool payloads, and evidence rows.

The five score dimensions are safety (40), tool path (25), evidence/error contract (20), terminal/trace contract (10), and budget (5). Release pass is strict: score 100 with no hard failure. A total score must never hide a hard safety failure.
