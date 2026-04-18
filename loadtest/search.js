// k6 load test: POST /api/v1/search/hub
//
// Run:
//   k6 run loadtest/search.js
//   API_BASE=http://staging.example.com k6 run --vus 50 --duration 5m loadtest/search.js
//
// Output JSON for baseline comparison:
//   k6 run --summary-export=loadtest/results/search.json loadtest/search.js

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const API_BASE = __ENV.API_BASE || 'http://localhost:8000';
const QUERIES = [
    '쿠버네티스 pod 재시작 방법',
    'API 502 에러 디버깅',
    '데이터마트 ETL 절차',
    'Confluence 검색이 안돼요',
    '담당자가 누구인가요',
    '신규 프로젝트 등록',
    'JIRA 티켓 자동 생성',
];

export const options = {
    // Default profile — override with --vus / --duration
    stages: [
        { duration: '30s', target: 10 },   // ramp up
        { duration: '1m',  target: 10 },   // sustain
        { duration: '30s', target: 30 },   // burst
        { duration: '1m',  target: 30 },   // sustain burst
        { duration: '30s', target: 0 },    // ramp down
    ],
    thresholds: {
        // Hard SLO failure = exit 1
        http_req_failed:   ['rate<0.01'],            // <1% errors
        http_req_duration: ['p(95)<2000', 'p(99)<5000'],  // p95<2s, p99<5s
    },
};

const ragLatency = new Trend('rag_search_duration_ms', true);

export default function () {
    const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
    const payload = JSON.stringify({
        query: query,
        top_k: 5,
        include_answer: false,  // skip LLM for pure retrieval load
    });

    const params = {
        headers: { 'Content-Type': 'application/json' },
        timeout: '30s',
    };

    const res = http.post(`${API_BASE}/api/v1/search/hub`, payload, params);

    check(res, {
        'status 200':              (r) => r.status === 200,
        'has chunks field':        (r) => r.json('chunks') !== undefined,
        'response < 5s':           (r) => r.timings.duration < 5000,
    });

    if (res.status === 200) {
        ragLatency.add(res.timings.duration);
    }

    sleep(Math.random() * 2 + 1);  // 1~3s pacing per VU
}
