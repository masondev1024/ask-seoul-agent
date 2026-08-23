# Contributing

ASK Seoul Agent는 `dev`에서 개발하고 `main`에서 release하는 단일 저장소 workflow를 사용합니다.

## Branch policy

- `dev`: 기능 통합 브랜치입니다. 모든 일반 개발 변경은 이 브랜치에 반영합니다.
- `main`: release 브랜치입니다. 직접 push하지 않고, CI를 통과한 `dev` release PR만 반영합니다.
- `feature/*`, `fix/*`, `docs/*`: 짧은 단위 작업을 위한 브랜치입니다. 기준 브랜치는 `dev`입니다.

`main`에 직접 작업 커밋을 쌓지 않습니다. GitHub에서는 작업 브랜치 → `dev` pull request, `dev` → `main` release pull request 흐름을 권장합니다.

## Development

```bash
git fetch origin
git switch dev
git pull --ff-only origin dev
git switch -c feature/<short-name>

uv sync --locked
make ci
```

변경을 push할 때는 작업 브랜치의 CI 결과를 확인하고 `dev`로 merge합니다. 라이브 Gemini/Anthropic 평가 명령은 비용이 발생하므로 필요한 경우에만 명시적으로 실행하고, `.env`와 API key는 절대 커밋하지 않습니다.

## Release

release 전 `dev`에서 다음 게이트를 통과해야 합니다.

```bash
git switch dev
git pull --ff-only origin dev
make ci
PYTHONPATH=src uv run --locked python -m ask_seoul_agent.eval_cli deterministic \
  --manifest evals/cases.v1.json \
  --output /tmp/ask-seoul-deterministic.json
```

그 다음 `dev`에서 `main` release PR을 열고 CI가 통과하면 merge합니다. `main` 보호 규칙은 직접 push를 막고 PR 기록을 남깁니다.

```bash
git push origin dev
gh pr create --base main --head dev --title "release: <summary>" --body "CI와 deterministic eval 통과"
gh pr checks --watch
gh pr merge --merge --delete-branch=false

# release 후 dev 동기화
git switch dev
git pull --ff-only origin dev
git pull --ff-only origin main
git push origin dev
```

배포가 필요하면 release된 `main` 커밋의 Docker image를 빌드합니다.

```bash
docker compose up --build --detach
curl --fail --silent http://127.0.0.1:8000/health/ready
```

## Scope and data safety

- 현재 agent는 운영 중인 weather 제품 4개만 사용합니다.
- 답변은 ASK Seoul preview evidence가 있을 때만 생성합니다.
- `demo` provider는 비용 없는 결정론적 검증용이고 LLM이 아닙니다.
- `reports/`, `omx_wiki/`, `.env`는 로컬 전용입니다.
