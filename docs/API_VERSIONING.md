# API Versioning Policy

axiomedge API 의 버전 관리 / breaking change / deprecation 절차.

## 버전 네이밍

- **`/api/v1/...`** — 현재 안정 버전 (모든 기존 엔드포인트)
- **`/api/v2/...`** — 차기 버전 (breaking change 누적 시 도입)

새 엔드포인트는 backward-compatible 이면 v1 에 추가 가능.
Breaking change 가 있으면 반드시 v2 로 분리.

## Breaking change 정의

**Breaking** (새 버전 prefix 필수):
- 응답 필드 제거 / 이름 변경
- 응답 필드 타입 변경 (string → object 등)
- 필수 request 필드 추가
- 기본 동작 변경 (예: 검색 default top_k 5→10)
- 기존 status code 의 의미 변경
- 인증 방식 강제 변경

**Non-breaking** (v1 에 추가 가능):
- 응답에 새 optional 필드 추가
- 새 옵셔널 query/body 파라미터
- 새 엔드포인트
- 에러 메시지 텍스트 개선
- 성능 개선

## Deprecation 절차

1. v2 에 대체 엔드포인트 배포
2. v1 라우트에 deprecation 등록 (`src/api/middleware/api_version.py`):
   ```python
   from src.api.middleware.api_version import deprecate

   deprecate(
       "/api/v1/legacy/search",
       sunset="2026-12-31",
       successor="/api/v2/search",
       note="v1 search 는 confidence 필드가 string. v2 는 float 으로 정확.",
   )
   ```
3. `Deprecation: true` + `Sunset: <date>` + `Link: </api/v2/search>; rel="successor-version"` 헤더 자동 부여
4. 최소 **6개월** 운영 (고객 마이그레이션 시간 확보)
5. Sunset 일에 v1 엔드포인트 410 Gone 응답으로 교체

## 클라이언트 측 처리

- Deprecation 헤더 발견 시 모니터링/로깅
- Sunset 60일 전부터 알림 → 마이그레이션 시작
- v2 prefix 는 점진적 전환 가능 (엔드포인트 단위)

## 참조

- RFC 8594 — Sunset HTTP Header Field
- IETF draft — Deprecation HTTP Header Field
- 구현: `src/api/middleware/api_version.py`
