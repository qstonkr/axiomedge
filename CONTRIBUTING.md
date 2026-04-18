# Contributing Guide

## 개발 환경 설정

```bash
# 1. Python 3.12+ 및 uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 의존성 설치
make setup

# 3. 인프라 시작 (PostgreSQL, Qdrant, Neo4j, Ollama, Redis, TEI, PaddleOCR)
make start

# 4. API 서버 실행
make api

# 5. 대시보드 실행 (별도 터미널)
make dashboard
```

## 코드 스타일

- **Ruff**: Python 3.12 타겟, 라인 길이 100자
- **검증**: `uvx ruff check src/` — 반드시 클린해야 합니다
- **Async**: 라우트, 리포지토리, 서비스 메서드 모두 `async def` 사용
- **CPU-bound 작업**: `asyncio.to_thread()`로 래핑

```bash
# 린트 실행
uvx ruff check src/

# 자동 수정
uvx ruff check --fix src/
```

## SSOT (Single Source of Truth) 규칙

하드코딩 금지. 반드시 중앙 설정 참조:

| 값 | 참조 위치 |
|---|---|
| 임베딩 차원 (1024) | `config_weights.EmbeddingConfig.dimension` |
| LLM 모델명 | `config.DEFAULT_LLM_MODEL` |
| DB URL | `config.DEFAULT_DATABASE_URL` |
| 신뢰도 임계값 | `config_weights.ConfidenceConfig` |
| 프롬프트 템플릿 | `src/search/tiered_response.py` |

전체 목록은 CLAUDE.md의 SSOT 테이블을 참고하세요.

## 새 코드 추가 패턴

### 새 API 라우트

1. `src/api/routes/`에 라우터 생성
2. `src/api/app.py`에 `include_router()` 등록
3. 필요시 `src/api/state.py`의 `AppState`에 필드 추가

### 새 서비스/프로바이더

1. 기존 Protocol 충족 확인 (`EmbeddingProvider`, `LLMClient`, `GraphRepository`, `ICacheLayer`)
2. `src/api/app.py`의 적절한 `_init_*()` 함수에 초기화 추가
3. `AppState`에 필드 추가

### 새 리포지토리

1. `src/database/repositories/base.py`의 `BaseRepository` 상속
2. `_init_database()`에서 초기화

## 테스트

```bash
# 전체 테스트
make test

# 단위 테스트만 (서비스 불필요)
make test-unit

# 통합 테스트 (API 필요)
make test-integration

# E2E 테스트 (전체 서비스 필요)
make test-e2e

# 개별 테스트
uv run pytest tests/unit/test_foo.py::test_bar -v --no-cov
```

### 테스트 작성 규칙

- 단위 테스트: `tests/unit/` — 외부 서비스 의존성 없음
- 통합 테스트: `tests/integration/` — API 서버 필요
- E2E 테스트: `tests/e2e/` — 전체 인프라 필요
- `pytest-asyncio` auto 모드 사용

## PR 프로세스

1. 기능 브랜치 생성: `git checkout -b feature/my-feature`
2. 코드 변경 및 테스트 작성
3. `uvx ruff check src/` 통과 확인
4. `make test-unit` 통과 확인
5. PR 생성 — 변경 사항 요약 포함
6. 리뷰 후 머지

## 커밋 메시지 컨벤션

```
<동작> <대상> (간결한 설명)

예시:
Add identifier search + document diversity for chunk-level recall
Fix 6 reliability issues flagged by static analysis
Improve golden set prompt: require store names and specific context
```

- `Add`: 새 기능
- `Fix`: 버그 수정
- `Improve`/`Update`: 기존 기능 개선
- `Refactor`: 동작 변경 없는 구조 개선
