## 변경 사항
<!-- 무엇을 왜 변경했는지 간단히 설명 -->

## 변경 범위
<!-- 해당하는 항목에 체크 -->
- [ ] 검색/RAG (`src/search/`, `src/llm/`, `src/embedding/`)
- [ ] 파이프라인 (`src/pipeline/`, `cli/`)
- [ ] 인증/보안 (`src/auth/`)
- [ ] 프론트엔드 (`dashboard/`)
- [ ] 인프라 (`k8s/`, `helm/`, `.github/`)
- [ ] 공통 (`src/config*.py`, `src/api/`, `src/database/`)
- [ ] 테스트/문서

## 테스트
- [ ] `uv run pytest tests/unit/ --no-cov` 통과
- [ ] 관련 테스트 추가/수정

## 체크리스트
- [ ] SSOT 규칙 준수 (하드코딩 없음)
- [ ] 기존 import 호환성 유지
- [ ] lint 통과 (`uvx ruff check`)
