# Admin E2E

19 admin 페이지 + 3 인터랙션 + 1 mutation 패턴 통합 검증.

## 실행

```bash
# 1) 사전 조건: backend 띄우기
make api          # FastAPI :8000
make start        # Postgres / Qdrant / Neo4j / Redis

# 2) 사용자 web dev 서버
cd src/apps/web
pnpm dev          # :3000

# 3) e2e 실행 (다른 터미널)
cd src/apps/web
PLAYWRIGHT_BASE_URL=http://localhost:3000 pnpm test:e2e:admin
```

PLAYWRIGHT_BASE_URL 안 주면 playwright 가 dev 서버 자체를 띄움 (config 의 webServer).

## 구성

- `smoke.spec.ts` — 19개 admin 페이지가 200 + 페이지 라벨 + 콘솔 에러 없음 (1차 회귀 보호)
- `interactions.spec.ts` — 탭 전환 (Edge 6 / Conflicts / Graph), 다이얼로그 open/close (sources / groups / users / jobs), KB 셀렉터 동적 로드 (quality / lifecycle / owners / graph-schema), ⌘K palette
- `glossary.spec.ts` — dialog form → POST mutation 패턴 1개 대표 검증

## 테스트 데이터 / cleanup

- 모든 mutation 은 `e2e-{ts}-{label}` prefix 로 데이터 생성
- 각 spec 이 가능한 곳에서 self-cleanup (UI delete)
- 마지막 안전망: `global-teardown.ts` 가 SQL 로 `e2e-%` LIKE 매칭 일괄 삭제 (postgres 컨테이너 직접)

## 알려진 제약

- Glossary 페이지는 223k 행 가진 환경에서 client-side 필터가 첫 100 행만 비추므로, "신규 등록한 e2e term 이 list 에 보임" 검증은 비현실적. 대신 POST 응답 200 + body.success=true 로 대체.
- Quality / Lifecycle 같은 KB 의존 페이지는 활성 KB 가 없으면 `test.skip` 처리됨 (4 skip 자연스러움).
