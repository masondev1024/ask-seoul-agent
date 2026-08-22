# Contracts

이 문서는 `ask-seoul-agent`가 외부 시스템과 맺는 런타임 계약을 정리합니다. README보다 좁게 API, tool, evidence, 검증 경계를 다룹니다.

## Runtime Modes

| mode | 목적 | live LLM 여부 | 준비 조건 |
| --- | --- | --- | --- |
| `demo` | 로컬 E2E, UI, SSE, tool loop 검증 | 아니오 | 없음 |
| `gemini` | Gemini GenerateContent API function-calling 실행 | 예 | `GEMINI_API_KEY` 필요; 기본 `GEMINI_MODEL=gemini-2.5-flash` |
| `anthropic` | Anthropic Messages API tool-use 실행 | 예 | `ANTHROPIC_API_KEY` 필요 |

`demo` provider는 `search_products -> preview_product -> final` 흐름을 결정적으로 재현합니다. 이 mode의 결과를 “LLM 답변”이라고 부르면 안 됩니다.

`gemini`/`anthropic` mode에서 선택한 provider의 key가 없으면 `/health/ready`와 chat endpoint는 503으로 fail-closed하며 `demo`로 묵시적으로 fallback하지 않습니다. 실제 key는 저장소에 커밋하지 않고 로컬 `.env`에서만 주입합니다.

## Endpoint Contract

| endpoint | method | 설명 |
| --- | --- | --- |
| `/health/live` | GET | 프로세스 생존 확인 |
| `/health/ready` | GET | provider 준비 상태 확인 |
| `/api/v1/meta` | GET | provider mode, ready 여부, tool 목록 |
| `/api/v1/chat/stream` | POST | SSE 기반 agent 실행 |

`POST /api/v1/chat/stream` request:

```json
{"question":"weather risk preview"}
```

validation:

- `question`은 1-500자
- 앞뒤 공백은 제거
- control character 포함 시 422

## SSE Events

| event | client 처리 기준 |
| --- | --- |
| `session.start` | trace id 저장, provider mode 표시 |
| `assistant.status` | 진행 상태 갱신 |
| `tool.call` | 어떤 tool이 어떤 입력으로 호출됐는지 표시 |
| `tool.result` | client-safe 결과 요약 표시 |
| `session.error` | 오류 panel에 표시. final event가 뒤따를 수 있음 |
| `assistant.final` | answer/evidence 갱신 |
| `session.done` | loading 종료, evidence status 표시 |

모든 event payload에는 `trace_id`가 포함됩니다.

## Tool Contract

현재 allowlist:

| tool | input | upstream |
| --- | --- | --- |
| `search_products` | `{"query": "1-200 chars"}` | `GET https://ask-seoul.kr/api/v1/search?q=...` |
| `preview_product` | `{"product_id": "^[A-Za-z][A-Za-z0-9_]{0,127}$"}` | `GET https://ask-seoul.kr/api/v1/preview/{product_id}` |

중요 제약:

- `preview_product`는 같은 request에서 `search_products`로 발견한 product id만 허용합니다.
- unknown tool은 upstream 호출 전에 거부합니다.
- tool result는 bounded mapping으로 잘라 prompt/output 크기를 제한합니다.
- 모델은 SQL, arbitrary URL, arbitrary product id를 직접 실행할 수 없습니다.

## Evidence Contract

`FinalEnvelope.evidence_status`:

| value | 의미 |
| --- | --- |
| `grounded_preview` | 최소 1개 preview evidence가 있음 |
| `insufficient_data` | 근거 preview가 없어 답변을 생성하지 않음 |

`Evidence`:

```json
{
  "tool": "preview_product",
  "product_id": "weather_place_risk_window",
  "source": "https://ask-seoul.kr/api/v1/preview/weather_place_risk_window",
  "request_id": "req_...",
  "freshness": "2026-08-20 11:23:50.065865",
  "row_count": 5,
  "sample_only": true,
  "rows": []
}
```

`sample_only`는 항상 `true`입니다. 현재 단계의 preview evidence는 전체 데이터 분석 결과가 아니라 공개 5행 샘플입니다.

## Failure Mapping

| internal code | public handling |
| --- | --- |
| `provider_auth` | provider credentials 문제 |
| `provider_rate_limited` | retryable provider 제한 |
| `provider_timeout` | retryable provider timeout |
| `provider_protocol_error` | malformed provider response/tool call rejected before execution |
| `agent_timeout` | shared agent/tool deadline exhausted; terminal fail-closed response |
| `upstream_timeout` | ASK Seoul timeout |
| `upstream_transport_error` | ASK Seoul network/transport 오류 |
| `upstream_retryable_status` | ASK Seoul 429/5xx retry exhausted |
| `upstream_quality_error` | public event에서는 `upstream_quality` |
| `upstream_schema_error` | ASK Seoul response contract drift |
| `agent_internal_error` | 예상하지 못한 provider 예외를 sanitized error로 변환 |
| `tool_internal_error` | 예상하지 못한 tool 예외를 sanitized error로 변환 |
| `unknown_tool` | allowlist 밖 tool 요청 |
| `product_not_discovered` | search 없이 preview 요청 |
| `tool_budget_exceeded` | tool call budget 초과 |
| `round_budget_exceeded` | model round budget 초과 |

Empty preview rows are not evidence. `FinalEnvelope` ignores empty preview payloads and returns `insufficient_data` instead of passing through provider prose.

## Browser Security Headers

All FastAPI responses include defensive browser headers:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: camera=(), geolocation=(), microphone=()`
- Content Security Policy scoped to self, FastAPI docs assets, and required inline style/CDN allowances

## Logging Contract

`ask_seoul_agent.observability.JsonFormatter` emits JSON logs with an explicit allowlist. Arbitrary `LogRecord` extras are not serialized.

Allowed operational fields include:

- `trace_id`
- `provider`
- `status`
- `elapsed_ms`
- `tool_count`
- `tool_name`
- `tool_call_id`
- `evidence_status`
- `input_tokens`
- `output_tokens`
- `request_id`
- `upstream_status`
- `retry_count`
- `exception_type`

## Verified Commands

```bash
cd ask-seoul-agent
uv run pytest -q
uv run ruff check .
uv run mypy src/ask_seoul_agent

cd frontend
npm test -- --run
npm run typecheck
npm run build
```

외부 호출은 기본 검증 명령과 CI에서 제외됩니다. Gemini live lane은 로컬 `.env`를 읽더라도 opt-in flag를 별도로 요구합니다.

```bash
AGENT_EVAL_LIVE=1 PYTHONPATH=src uv run --env-file .env --locked python \
  -m ask_seoul_agent.eval_cli live \
  --provider gemini \
  --manifest evals/cases.v1.json \
  --output evals/results/live-latest.json \
  --inter-case-delay-seconds 75

AGENT_LIVE_E2E=1 uv run --env-file .env --locked pytest -q -m live \
  -k gemini_and_ask_seoul
```

rate limit/quota/transport 실패는 `inconclusive_infra`로 분류해 모델의 behavioral failure와 분리합니다. 무료 티어 pacing은 케이스 사이에만 적용하며, 한 케이스의 행동 실패를 재시도하지 않습니다. live report에는 prompt, raw evidence row, tool payload, key를 저장하지 않습니다.

검증 결과는 README의 “Verification Results” 섹션을 기준으로 갱신합니다.
