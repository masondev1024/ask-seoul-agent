# Contracts

이 문서는 `ask-seoul-agent`가 외부 시스템과 맺는 런타임 계약을 정리합니다. README보다 좁게 API, tool, evidence, 검증 경계를 다룹니다.

## Runtime Modes

| mode | 목적 | live LLM 여부 | 준비 조건 |
| --- | --- | --- | --- |
| `demo` | 로컬 E2E, UI, SSE, tool loop 검증 | 아니오 | 없음 |
| `gemini` | Gemini GenerateContent API function-calling 실행 | 예 | `GEMINI_API_KEY` 필요; 기본 `GEMINI_MODEL=gemini-2.5-flash` |
| `anthropic` | Anthropic Messages API tool-use 실행 | 예 | `ANTHROPIC_API_KEY` 필요 |

`demo` provider는 현재 서빙 중인 ASK Seoul weather 제품 4개에 대해 `search_products -> preview_product -> final` 흐름을 결정적으로 재현합니다. 이 mode의 결과를 “LLM 답변”이라고 부르면 안 됩니다.

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
{"question":"서울 주요 장소의 폭염이나 호우 위험 시간대를 알려줘"}
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

현재 tool allowlist:

| tool | input | upstream |
| --- | --- | --- |
| `search_products` | `{"query": "1-200 chars"}` | `GET https://ask-seoul.kr/api/v1/catalog` |
| `preview_product` | `{"product_id": "<4-product enum>"}` | `GET https://ask-seoul.kr/api/v1/preview/{product_id}` |

현재 product allowlist:

| product_id | 한국어 이름 | 용도 |
| --- | --- | --- |
| `weather_place_current_outlook` | 장소별 현재 날씨 전망 | 현재/지금/오늘 날씨 상태 질문 |
| `weather_place_forecast_change_daily` | 장소별 일간 예보 변화 | 직전 발표 대비 예보 변화 질문 |
| `weather_place_precipitation_window` | 장소별 강수 예상 시간대 | 비/눈/강수 예상 시간대 질문 |
| `weather_place_risk_window` | 장소별 기상 위험 예상 시간대 | 폭염/한파/호우/대설/강풍 등 위험 질문 |

중요 제약:

- tool schema는 execution state에 따라 단계적으로 노출한다.
- 초기 provider turn에는 `search_products`만 제공한다.
- `search_products`가 non-empty 후보를 반환한 뒤에만 `preview_product`를 제공한다.
- `preview_product` 실행 후, search 결과가 비었을 때, 또는 tool/provider/upstream error 후에는 더 이상 tool을 제공하지 않는다.
- `search_products`는 catalog 응답에서 위 4개 weather product만 bounded order로 노출합니다.
- 질문이 weather intent가 아니면 후보를 빈 배열로 반환하고 다른 도메인으로 fallback하지 않습니다.
- `preview_product`는 같은 request에서 `search_products`로 발견한 위 4개 weather product id만 허용합니다.
- transit, traffic, population, culture 등 다른 product id는 upstream preview 호출 전에 거부합니다.
- unknown tool은 upstream 호출 전에 거부합니다.
- 현재 단계에서 provider가 노출되지 않은 tool을 요청하면 upstream 실행 전에 `provider_protocol_error`로 fail-closed한다.
- tool result는 bounded mapping으로 잘라 prompt/output 크기를 제한합니다.
- 모델은 SQL, arbitrary URL, arbitrary product id를 직접 실행할 수 없습니다.
- 최종 답변과 브라우저 UI의 scope/error 메시지는 한국어로 표시합니다. API error code는 안정적인 영문 machine code를 유지합니다.

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
| `unsupported_product_scope` | 4개 weather product 밖 preview 요청 |
| `tool_budget_exceeded` | tool call budget 초과 |
| `round_budget_exceeded` | model round budget 초과 |

Empty preview rows are not evidence. `FinalEnvelope` ignores empty preview payloads and returns `insufficient_data` instead of passing through provider prose.

`weather_place_current_outlook`처럼 upstream 품질 게이트가 닫힌 제품은 503 `product_not_ready` 또는 freshness/publication 관련 payload를 반환할 수 있습니다. 이 경우 service는 `upstream_quality_error`로 분류하고, retry나 다른 도메인 fallback 없이 evidence 없는 한국어 fail-closed 응답으로 끝냅니다.

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
uv run pytest -q tests/test_weather_scope_contract.py
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

rate limit/quota/transport 실패는 `inconclusive_infra`로 분류해 모델의 behavioral failure와 분리합니다. 필요하면 케이스 사이 pacing을 줄 수 있지만, 한 케이스의 행동 실패는 재시도하지 않습니다. live report에는 prompt, raw evidence row, tool payload, key를 저장하지 않습니다.

30-case deterministic suite는 AgentRunner/FastAPI/tool/evidence control-plane 회귀평가입니다. Weather scope contract tests는 별도로 4개 product allowlist, Korean routing, transit reject, preview evidence 경계를 검증합니다.

검증 결과는 README의 “Verification Results” 섹션을 기준으로 갱신합니다.
