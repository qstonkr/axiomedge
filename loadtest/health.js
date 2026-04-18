// k6 sanity load: GET /health
// Quick check that infra holds — should always be sub-50ms p95.

import http from 'k6/http';
import { check } from 'k6';

const API_BASE = __ENV.API_BASE || 'http://localhost:8000';

export const options = {
    vus: 20,
    duration: '30s',
    thresholds: {
        http_req_failed:   ['rate==0'],
        http_req_duration: ['p(95)<50', 'p(99)<200'],
    },
};

export default function () {
    const res = http.get(`${API_BASE}/health`);
    check(res, { 'status 200': (r) => r.status === 200 });
}
