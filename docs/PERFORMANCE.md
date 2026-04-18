# Performance Testing

k6 기반 부하 테스트 + 베이스라인 회귀 차단.

## 빠른 명령

```bash
# k6 설치 (한 번만)
brew install k6  # macOS

# 로컬 부하 테스트 — 기본 30s 워밍업 + 1m 베이스 + 30s 부스트 + 1m 부스트 + 30s 쿨다운
k6 run loadtest/search.js
k6 run loadtest/health.js

# 베이스라인 비교 — 결과 JSON 추출 후
k6 run --summary-export=loadtest/results/search.json loadtest/search.js
python scripts/perf_check.py loadtest/results/search.json --scenario search.js

# 다른 환경
API_BASE=http://staging.example.com k6 run loadtest/search.js
```

## Baseline (`loadtest/baseline.json`)

| 시나리오 | p50 | p95 | p99 | 에러율 | 처리량 |
|---|---|---|---|---|---|
| `search.js` (RAG retrieval) | 800ms | 1.8s | 4.5s | 0.5% | ≥8 RPS |
| `health.js` | 5ms | 30ms | 100ms | 0% | ≥100 RPS |

기본 tolerance 20% — 베이스라인 대비 latency/error rate 가 1.2배 초과 시 fail.

## CI 통합

부하 테스트는 **수동 trigger** 만 — PR 마다 돌리기엔 비싸고 환경 의존도 높음.

```bash
# GitHub Actions UI 또는:
gh workflow run perf.yml -f scenario=search.js
```

## 시나리오 작성 가이드

새 시나리오 추가 시:
1. `loadtest/X.js` — k6 ES module
2. `loadtest/baseline.json` 에 expected 메트릭 추가
3. README 업데이트

기준선 갱신 (실제 측정값이 좋아져 baseline 끌어올리고 싶을 때):
```bash
make perf-update-baseline SCENARIO=search.js  # last_run.json → baseline 의 해당 키
```

## 한계

- **로컬 macOS** 측정값 ≠ Linux production 성능. 베이스라인은 **고정 환경**(staging/prod) 기준이어야 의미.
- LLM 호출은 비용이 커 `include_answer=false` 로 retrieval 만 부하. LLM 포함 부하는 별도 시나리오 + 비용 예산.
- 동시 사용자 수가 진짜 capacity 신호. VU=10/30 은 sanity 수준 — 실 capacity planning 은 staging 에서 100~1000 VU 별도.
