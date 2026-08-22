# NOTICE

이 문서는 법률 자문이나 라이선스 부여 문구가 아닙니다. 이 저장소가 어떤 upstream과 데이터 조건을 전제로 동작하는지 기록하기 위한 provenance notice입니다.

## Project Boundary

`ask-seoul-agent`는 ASK Seoul 데이터 상품을 agent가 소비할 수 있게 만든 개인 확장 저장소입니다.

- 조직 프로젝트의 애플리케이션 코드를 복사하지 않았습니다.
- ASK Seoul live service의 공개 REST API 계약을 client adapter로 호출합니다.
- 현재 구현은 public search/preview만 사용하며, MCP/authenticated query는 아직 포함하지 않습니다.
- `demo` provider는 LLM이 아니며, 로컬 E2E 검증을 위한 결정적 provider입니다.
- Anthropic live API 호출은 API key가 없어 아직 검증하지 않았습니다.

## Upstream Attribution

이 저장소는 다음 프로젝트와 서비스의 공개 계약 또는 문서를 참조합니다.

- ASK Seoul Dashboard: https://github.com/ASAC-DE-bigkk/ASK-Seoul-Dashboard
- ASK Seoul Serving: https://github.com/ASAC-DE-bigkk/ASK-Seoul-Serving
- NomaDamas k-skill: https://github.com/NomaDamas/k-skill
- ASK Seoul live service: https://ask-seoul.kr
- Anthropic Tool Use documentation: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview

## Public Data Conditions

ASK Seoul 데이터 상품은 서울열린데이터광장, 기상청, TOPIS, KOPIS 등 공개 API와 공공 데이터셋을 기반으로 만들어진 것으로 이해하고 있습니다. 각 원천 데이터의 이용 조건은 데이터셋별 메타데이터와 제공기관 고지를 우선해야 합니다.

참고 링크:

- 공공누리 유형 안내: https://www.kogl.or.kr/info/license.do
- 공공누리 제1유형 출처표시: https://www.kogl.or.kr/info/licenseType1.do
- 서울열린데이터광장 이용안내: https://data.seoul.go.kr/etc/openInfo.do
- 서울열린데이터광장 이용약관: https://data.seoul.go.kr/etc/accessTerms.do
- 공공데이터포털: https://www.data.go.kr
- k-skill source notes: https://github.com/NomaDamas/k-skill/blob/main/docs/sources.md

데이터를 재사용할 때는 각 데이터 상품이 노출하는 source, freshness, license/attribution metadata를 확인해야 합니다.

## Contributions in This Repository

이 저장소의 구현 및 문서 기여 범위는 다음과 같습니다.

- FastAPI SSE transport
- provider-neutral agent runner
- deterministic demo provider
- Anthropic Messages API adapter
- ASK Seoul public REST client
- allowlisted tool registry
- evidence envelope and fail-closed response contract
- React console UI
- backend/frontend contract tests and local verification documentation
- multi-stage container image, hardened Compose runtime, and build-only CI workflow

이 NOTICE는 upstream 프로젝트나 공공 데이터 제공기관의 라이선스를 변경하거나 대체하지 않습니다.
