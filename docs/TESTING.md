# Testing Guide

**생성**: 2026-04-16 (PR6 — 커버리지 기준선 수립)
**베이스라인**: 77.0% line coverage on `src/` (5,631 passing unit tests)
**Floor**: 75% (global, monotonic 상승), 80% (touched file per PR)

이 문서는 knowledge-local 의 테스트 정책 + 커버리지 gate + 작성 가이드
single source of truth 입니다. `docs/IMPROVEMENT_PLAN.md` 의 제약 사항 #6
"테스트 커버리지 ≥ 80%" 를 실제로 enforce 하는 메커니즘.

---

## 목차

1. [테스트 레이어](#테스트-레이어)
2. [커버리지 정책](#커버리지-정책)
3. [CI gate 작동](#ci-gate-작동)
4. [실행 방법](#실행-방법)
5. [테스트 작성 가이드](#테스트-작성-가이드)
6. [pragma: no cover 허용 기준](#pragma-no-cover-허용-기준)
7. [Backfill 대상 파일](#backfill-대상-파일-phase-c)
8. [알려진 flaky / 기존 fail](#알려진-flaky--기존-fail)

---

## 테스트 레이어

| 디렉터리 | 역할 | 외부 의존성 | 목표 실행 시간 |
|---|---|---|---|
| `tests/unit/` | 대부분의 로직 — mock 기반 | 없음 | < 60s 전체 |
| `tests/integration/` | 실제 서비스 연결 (Qdrant/Neo4j/PG) | Docker services 필요 | < 5min 전체 |
| `tests/e2e/` | API + dashboard 전체 flow | 모든 서비스 + LLM | 선택적 |

**비중 가이드라인**: Unit 80% · Integration 15% · E2E 5%

### 왜 이 비중인가

- Unit 은 빠르고 결정적이라 PR 때마다 돌 수 있음 (GitHub Actions checks)
- Integration 은 느려서 manual trigger 또는 nightly 로 제한
- E2E 는 모든 서비스가 떠 있는 통합 환경에서만 의미 있음

---

## 커버리지 정책

### 측정 범위

- **대상**: `src/**/*.py` 전부
- **제외**:
  - `dashboard/` — Streamlit (곧 SPA 로 교체 예정, ROI 낮음)
  - `scripts/` — one-off 유틸리티
  - `cli/` — 얇은 entry point (선택적 측정)
- **도구**: `pytest-cov` (`coverage.py` 기반)
- **설정 위치**: `pyproject.toml::[tool.coverage.run]`, `[tool.coverage.report]`

### Floor 상승 스케줄

| 시점 | Global floor | Touched file 요구 |
|---|---|---|
| 지금 (베이스라인) | **75%** (현재 77% 에서 margin) | ≥ 80% |
| Phase A 종료 | 75% | ≥ 80% |
| Phase B 종료 | 80% | ≥ 80% |
| Phase C 종료 (backfill 완료) | 85% | ≥ 80% |

Floor 는 `pyproject.toml::fail_under` 에서 관리. Phase 단계별로 올릴 때
별도 PR 로 수정 + 이 표 갱신.

### 왜 global floor 75% / touched 80% 두 개인가

- **Global floor** (전체 평균): 베이스라인이 77% 이므로 안전 margin 으로 75%.
  한 PR 이 대규모 코드 삭제 / 재구조화 때 일시적 하락 허용.
- **Touched file floor** (개별 파일): PR 이 **건드린 파일은** 80% 이상 유지.
  이게 실질적인 "품질 방지턱" — 신규 파일 / 수정된 파일이 테스트 없이
  들어오는 것을 원천 차단.

---

## CI gate 작동

### 로컬

```bash
# 전체 unit tests + coverage 측정
make test-unit

# 또는 명시적
uv run pytest tests/unit/ --cov=src --cov-report=term-missing --cov-report=json:coverage.json

# Touched file gate (main 과 비교)
uv run python scripts/coverage_gate.py

# 다른 base 와 비교
uv run python scripts/coverage_gate.py --base origin/main
```

### CI (GitHub Actions)

Pipeline step:

```bash
uv sync
uv run pytest tests/unit/ \
    --cov=src \
    --cov-report=term-missing \
    --cov-report=json:coverage.json \
    --cov-report=html:htmlcov/ \
    --cov-fail-under=75  # global floor, monotonic

uv run python scripts/coverage_gate.py --base origin/main --threshold 80
```

두 단계:

1. `--cov-fail-under=75` — 전체 평균 떨어지면 CI fail
2. `scripts/coverage_gate.py` — PR 이 수정한 파일 중 80% 미만 있으면 CI fail

둘 중 하나라도 실패하면 merge 불가.

### Artifact

- `htmlcov/` — 브라우저로 라인별 커버리지 확인
- `coverage.json` — gate script 가 파싱
- `.coverage` — coverage.py 내부 binary (gitignore 됨)

---

## 실행 방법

### 빠른 피드백 (개발 중)

```bash
# 특정 파일만
uv run pytest tests/unit/test_prompt_safety.py -v --no-cov

# 특정 테스트
uv run pytest tests/unit/test_foo.py::TestBar::test_baz -v --no-cov

# 패턴 매칭
uv run pytest tests/unit/ -k "distill or reformat" --no-cov
```

`--no-cov` 는 coverage 측정 skip 해서 속도 향상. 로컬 빠른 iteration 용.

### PR 준비 (push 전)

```bash
# 전체 regression + coverage
make test-unit

# Touched file gate 로 현재 PR 이 지키는지 확인
uv run python scripts/coverage_gate.py
```

두 명령 모두 통과해야 push.

### Integration / E2E (서비스 필요)

```bash
# Docker services 먼저
make start

# Integration 만
uv run pytest tests/integration/ -m integration

# E2E
uv run pytest tests/e2e/ -m e2e
```

---

## 테스트 작성 가이드

### Unit test 기본 구조

```python
"""Tests for src/foo/bar — one-line description."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMyComponent:
    """src/foo/bar.py::MyComponent"""

    def _make(self):
        # Factory fixture — 반복되는 constructor 감싸기
        from src.foo.bar import MyComponent
        return MyComponent(dep=MagicMock())

    @pytest.mark.asyncio
    async def test_happy_path(self):
        comp = self._make()
        result = await comp.process({"key": "value"})
        assert result == expected

    @pytest.mark.asyncio
    async def test_error_path(self):
        comp = self._make()
        with pytest.raises(ValueError, match="missing key"):
            await comp.process({})
```

### Mock 패턴

- **Async dependency**: `AsyncMock()` — `return_value=...` 또는 `side_effect=...`
- **Sync dependency**: `MagicMock()`
- **DB session**: `AsyncMock` 으로 `session.execute`, `session.commit` 스텁
- **LLM client**: `AsyncMock(call=AsyncMock(return_value="..."))`

`tests/conftest.py` 에 있는 공용 fixtures 재사용:

- `mock_embedder` — BGE 호환 dummy
- `mock_vector_store` — 인메모리 Qdrant 스텁
- `mock_graph_store` — 인메모리 Neo4j 스텁
- `client` — FastAPI async test client (lifespan 없이)

### 경계 케이스 체크리스트

- [ ] Happy path (정상 입력 / 정상 결과)
- [ ] Empty input (`""`, `[]`, `None`)
- [ ] Invalid input (타입 오류, out-of-range)
- [ ] 실패하는 외부 의존성 (exception)
- [ ] 경계값 (min/max, first/last)
- [ ] Concurrency (해당 시)

### 네이밍

- `test_<behavior>` — 함수명은 검증하는 **행위** 서술
  - 좋음: `test_rejects_missing_base_model`
  - 나쁨: `test_1`, `test_create_profile_function`
- Class 로 묶기: `TestXxx` — 한 컴포넌트의 테스트 그룹

---

## `pragma: no cover` 허용 기준

**원칙**: 가능하면 테스트 작성. `pragma` 는 최후의 수단.

### 허용되는 경우

1. **Streamlit UI 렌더링**
   ```python
   def render_dashboard():  # pragma: no cover
       st.title(...)
   ```
   (Streamlit state 재현 불가능)

2. **`if __name__ == "__main__":` CLI entry**
   ```python
   if __name__ == "__main__":  # pragma: no cover
       main()
   ```

3. **외부 subprocess wrapper 의 바이너리 없음 fallback**
   ```python
   if not shutil.which("llama-quantize"):  # pragma: no cover
       logger.error(...)
       return None
   ```

4. **Protocol/ABC 스텁**
   ```python
   class MyProtocol(Protocol):
       async def process(self, x: int) -> str: ...  # pragma: no cover
   ```

### 금지되는 경우

- ❌ 로직이 있는 함수 전체에 pragma 씌우기
- ❌ `except Exception: pass` 같은 bare fallback (PR2 에서 제거한 패턴)
- ❌ "테스트 작성하기 귀찮아서" — 리뷰어가 reject

### 사용 시 주의

- Pragma 는 **해당 줄만** 제외. 블록에 씌우려면 각 줄마다 혹은 함수 시그니처에.
- PR 리뷰에서 **근거 주석** 필수 — "왜 테스트 불가능한가" 설명.

---

## Backfill 대상 파일 (Phase C)

2026-04-16 베이스라인 측정 결과, 100+ statement 파일 중 커버리지 낮은 순:

| % | 파일 | stmt | 비고 |
|---|---|---|---|
| 0.0% | `src/distill/gpu_trainer.py` | 102 | GPU 경로 — mock 필요 |
| 0.0% | `src/distill/trainer.py` | 117 | LoRA training — mock 필요 |
| 7.8% | `src/distill/service.py` | 449 | Phase 1.5 리팩터 PR9 와 묶어서 |
| 19.3% | `src/distill/repositories/training_data.py` | 119 | |
| 20.6% | `src/distill/repositories/edge_server.py` | 126 | |
| 21.1% | `src/distill/data_gen/test_data_templates.py` | 142 | |
| 28.3% | `src/api/routes/distill.py` | 755 | PR8 routes 분할 후 |
| 36.6% | `src/api/routes/search_helpers.py` | 273 | |
| 41.9% | `src/distill/quantizer.py` | 136 | subprocess 중심 |
| 59.6% | `src/api/routes/admin.py` | 428 | |
| 61.0% | `src/connectors/git/client.py` | 141 | |
| 61.2% | `src/database/repositories/glossary.py` | 209 | |
| 61.7% | `src/api/app.py` | 540 | lifespan / 초기화 |
| 62.6% | `src/api/routes/search.py` | 589 | PR9 stage 분할 후 |
| 64.0% | `src/api/routes/auth.py` | 317 | |
| 66.1% | `src/cv_pipeline/pipeline.py` | 271 | |
| 66.7% | `src/search/cross_encoder_reranker.py` | 111 | |
| 67.1% | `src/pipeline/ingestion.py` | 498 | PR9 stage protocol 후 |
| 67.5% | `src/api/routes/rag.py` | 292 | |
| 68.0% | `src/connectors/confluence/attachment_parser.py` | 952 | |

각 파일은 별도 PR 로 처리 (Phase C). 하나의 PR 에 여러 파일 backfill 금지 — 리뷰 부담.

**우선순위 원칙**: 비즈니스 중요도 × 현재 coverage gap. `src/distill/service.py` (449 stmt, 8%) 같은 곳은 리팩터 (Phase B PR9) 와 함께 처리하면 2-for-1.

---

## 알려진 flaky / 기존 fail

2026-04-16 PR6 측정 시 발견:

### 기존 fail (main 머지 이전부터 존재)

- `tests/unit/test_data_source_sync.py::TestRunIngestion::test_ingestion_with_mock_pipeline`
- `tests/unit/test_data_source_sync.py::TestRunIngestion::test_ingestion_with_errors`
- `tests/unit/test_document_parser_extended.py::TestParsePptx::test_parse_pptx_success`
  - `src/pipeline/document_parser.py:537` `AttributeError: 'list' object has no attribute 'title'`
  - python-pptx API change or mocking issue

### Flaky (재실행 시 간헐적 pass)

- `tests/unit/test_summary_tree_builder.py` — clustering embedding 관련 7 cases
  - K-means init seed 또는 embedding similarity 기반 → 첫 run 만 fail, 재실행 pass
  - Seed fix 필요

이 둘은 PR6 범위 밖. 별도 follow-up PR 에서 처리. `docs/IMPROVEMENT_PLAN.md`
Phase C 에 backlog 추가 예정.

---

## 참고

- `pyproject.toml::[tool.coverage.*]` — 설정 SSOT
- `scripts/coverage_gate.py` — touched file gate 구현
- `Makefile::test-unit` — 로컬 실행 wrapper
- `docs/IMPROVEMENT_PLAN.md` — 제약 사항 #6, Phase C backfill 목록
