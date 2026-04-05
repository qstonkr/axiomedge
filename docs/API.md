# API Reference

## Base URL

```
http://localhost:8000
```

In K8s: `http://knowledge-api.knowledge.svc:8000`

## Authentication

Authentication is controlled by `AUTH_ENABLED` and `AUTH_PROVIDER` environment variables.

When `AUTH_ENABLED=false` (default), all endpoints are accessible without credentials.

When enabled with `AUTH_PROVIDER=internal`:

```bash
# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme"}'
# Returns JWT in HttpOnly cookies (access_token + refresh_token)

# Subsequent requests include cookies automatically, or use:
curl -H "Authorization: Bearer <access_token>" http://localhost:8000/api/v1/search/hub
```

When `AUTH_PROVIDER=local`, pass the API key:

```bash
curl -H "X-API-Key: <key>" http://localhost:8000/api/v1/search/hub
```

## Key Endpoints

### Health

#### GET /health

Check service status.

```bash
curl http://localhost:8000/health
```

Response:

```json
{
  "status": "healthy",
  "checks": {
    "qdrant": true,
    "neo4j": true,
    "embedding": true,
    "llm": true,
    "redis": true,
    "database": true,
    "paddleocr": true
  }
}
```

`status` is `"healthy"` when Qdrant and embedding are up, `"degraded"` otherwise.

---

### Search

#### POST /api/v1/search/hub

Hybrid search across knowledge bases. This is the primary search endpoint.

```bash
curl -X POST http://localhost:8000/api/v1/search/hub \
  -H "Content-Type: application/json" \
  -d '{
    "query": "VPN connection troubleshooting",
    "kb_ids": ["itops"],
    "top_k": 5
  }'
```

Request body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Search query |
| `kb_ids` | string[] | no | Filter by knowledge base IDs |
| `top_k` | int | no | Max results (default: 5) |

Response:

```json
{
  "results": [
    {
      "id": "chunk-uuid",
      "content": "To troubleshoot VPN issues...",
      "score": 0.87,
      "metadata": {
        "kb_id": "itops",
        "source": "vpn-guide.pdf",
        "page": 3
      }
    }
  ],
  "query": "VPN connection troubleshooting",
  "total": 5
}
```

#### GET /api/v1/search/hub/kbs

List available knowledge bases for search.

```bash
curl http://localhost:8000/api/v1/search/hub/kbs
```

---

### RAG (Question Answering)

#### POST /api/v1/knowledge/ask

Ask a question and get an LLM-generated answer with source chunks.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the procedure for equipment disposal?",
    "kb_id": "itops"
  }'
```

Request body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Question |
| `kb_id` | string | no | Target knowledge base |
| `kb_ids` | string[] | no | Multiple knowledge bases |
| `mode` | string | no | `"classic"` (default) |

Response:

```json
{
  "query": "What is the procedure for equipment disposal?",
  "answer": "The equipment disposal procedure involves...",
  "chunks": [
    {
      "content": "...",
      "score": 0.92,
      "metadata": { "source": "policy.pdf", "kb_id": "itops" }
    }
  ],
  "mode": "classic"
}
```

#### GET /api/v1/knowledge/rag/config

Get current RAG pipeline configuration.

#### GET /api/v1/knowledge/rag/stats

Get RAG pipeline statistics.

---

### Ingestion

#### POST /api/v1/knowledge/ingest

Ingest documents from a server-side directory.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "kb_id": "my-kb",
    "source_dir": "/data/documents/",
    "force_rebuild": false
  }'
```

Request body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kb_id` | string | no | Knowledge base ID (default: `"knowledge"`) |
| `source_dir` | string | yes | Server-side directory path |
| `force_rebuild` | bool | no | Re-ingest all documents (default: false) |

Response:

```json
{
  "success": true,
  "kb_id": "my-kb",
  "documents_processed": 15,
  "chunks_created": 342,
  "errors": []
}
```

#### POST /api/v1/knowledge/upload

Upload files for ingestion.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "files=@document.pdf" \
  -F "kb_id=my-kb"
```

#### POST /api/v1/knowledge/file-upload-ingest

Upload and ingest files in one step (with job tracking).

#### POST /api/v1/knowledge/reingest-from-jsonl

Re-run Stage 2 ingestion from a JSONL checkpoint (useful after OCR crash recovery).

---

### KB Management

#### POST /api/v1/kb/create

Create a new knowledge base.

```bash
curl -X POST http://localhost:8000/api/v1/kb/create \
  -H "Content-Type: application/json" \
  -d '{"kb_id": "new-kb", "display_name": "New Knowledge Base"}'
```

#### GET /api/v1/kb/list

List all knowledge bases.

```bash
curl http://localhost:8000/api/v1/kb/list
```

#### DELETE /api/v1/kb/{kb_id}

Delete a knowledge base and all its data.

```bash
curl -X DELETE http://localhost:8000/api/v1/kb/itops
```

---

### Metrics

#### GET /metrics

Get system metrics.

```bash
# JSON format (default)
curl http://localhost:8000/metrics

# Prometheus text format
curl "http://localhost:8000/metrics?format=prometheus"
```

---

### Admin

#### GET /api/v1/admin/graph/stats

Get Neo4j graph statistics.

#### GET /api/v1/admin/qdrant/collections

List Qdrant collections.

#### GET /api/v1/admin/qdrant/collection/{name}/stats

Get collection statistics.

#### PUT /api/v1/admin/config/weights

Update search/ranking weights at runtime.

#### POST /api/v1/admin/config/weights/reset

Reset weights to defaults.

---

### Search Groups

#### GET /api/v1/search-groups

List all search groups.

#### POST /api/v1/search-groups

Create a search group (preset KB combinations for convenient multi-KB search).

```bash
curl -X POST http://localhost:8000/api/v1/search-groups \
  -H "Content-Type: application/json" \
  -d '{"name": "IT Ops All", "kb_ids": ["itops", "itops_general"]}'
```

#### GET /api/v1/search-groups/{group_id}

Get a specific search group.

#### PUT /api/v1/search-groups/{group_id}

Update a search group.

#### DELETE /api/v1/search-groups/{group_id}

Delete a search group.

#### GET /api/v1/search-groups/{group_id}/kbs

List KBs in a search group.

---

### Data Sources

#### GET /api/v1/data-sources

List all data sources.

#### POST /api/v1/data-sources

Register a new data source (Confluence, Git, SharePoint, etc.).

#### GET /api/v1/data-sources/{source_id}

Get data source details.

#### PUT /api/v1/data-sources/{source_id}

Update a data source configuration.

#### DELETE /api/v1/data-sources/{source_id}

Delete a data source.

#### POST /api/v1/data-sources/{source_id}/trigger

Trigger sync for a data source.

#### GET /api/v1/data-sources/{source_id}/status

Get sync status.

#### POST /api/v1/data-sources/file-ingest

Ingest from a file-based data source.

---

### Quality & Evaluation

#### GET /api/v1/admin/quality/golden-set

Get golden set items (reference Q&A pairs for evaluation).

```bash
curl "http://localhost:8000/api/v1/admin/quality/golden-set?kb_id=itops&limit=50"
```

#### PATCH /api/v1/admin/quality/golden-set/{item_id}

Update a golden set item.

#### DELETE /api/v1/admin/quality/golden-set/{item_id}

Delete a golden set item.

#### POST /api/v1/admin/quality/eval/trigger

Trigger RAG evaluation run against golden set.

#### GET /api/v1/admin/quality/eval/status

Get current evaluation run status.

#### GET /api/v1/admin/quality/eval/history

Get evaluation run history.

#### GET /api/v1/admin/quality/eval-results

Get detailed evaluation results (RAGAS metrics: faithfulness, context relevancy, answer relevancy, CRAG recommendation, recall).

#### GET /api/v1/admin/quality/eval-results/summary

Get evaluation results summary.

#### POST /api/v1/admin/quality/trust-scores/calculate

Calculate trust scores for documents in a KB.

#### GET /api/v1/admin/quality/dedup/stats

Get dedup pipeline statistics.

#### GET /api/v1/admin/quality/dedup/conflicts

List detected conflicts between documents.

#### POST /api/v1/admin/quality/dedup/resolve

Resolve a dedup conflict.

#### GET /api/v1/admin/quality/vectorstore/stats

Get Qdrant vector store statistics.

#### GET /api/v1/admin/quality/embedding/stats

Get embedding provider statistics.

#### GET /api/v1/admin/quality/cache/stats

Get multi-layer cache statistics.

#### GET /api/v1/admin/quality/transparency/stats

Get transparency/explainability statistics.

#### GET /api/v1/admin/quality/contributors

Get contributor activity statistics.

#### GET /api/v1/admin/quality/verification/pending

Get documents pending verification.

#### POST /api/v1/admin/quality/verification/{doc_id}/vote

Submit verification vote for a document.

---

### Pipeline & Ingestion Jobs

#### GET /api/v1/admin/pipeline/status

Get pipeline status.

#### GET /api/v1/admin/pipeline/metrics

Get pipeline metrics (processing times, success rates).

#### GET /api/v1/admin/pipeline/runs/{run_id}

Get details of a specific pipeline run.

#### POST /api/v1/admin/knowledge/ingest

Trigger ingestion from server-side directory.

#### GET /api/v1/admin/knowledge/ingest/jobs

List ingestion jobs.

#### GET /api/v1/admin/knowledge/ingest/status/{run_id}

Get ingestion job status.

#### POST /api/v1/admin/knowledge/ingest/jobs/{run_id}/cancel

Cancel a running ingestion job.

#### GET /api/v1/admin/ingestion/stats

Get ingestion statistics.

#### POST /api/v1/admin/kb/{kb_id}/sync

Trigger KB sync (incremental crawl + ingest).

#### POST /api/v1/admin/pipeline/publish/dry-run

Preview pipeline publish changes.

#### POST /api/v1/admin/pipeline/publish/execute

Execute pipeline publish.

#### GET /api/v1/admin/pipeline/gates/stats

Get quality gate statistics.

#### GET /api/v1/admin/pipeline/gates/blocked

Get documents blocked by quality gates.

---

### Search Analytics

#### GET /api/v1/analytics/history

Get search history.

#### GET /api/v1/analytics/analytics

Get search analytics (top queries, zero-result queries, CTR).

#### GET /api/v1/analytics/user-history

Get per-user search history.

#### GET /api/v1/analytics/crag-stats

Get CRAG (Corrective RAG) statistics.

#### GET /api/v1/analytics/injection-stats

Get prompt injection detection statistics.

#### GET /api/v1/analytics/agentic-rag-stats

Get agentic RAG statistics.

---

### Glossary

#### GET /api/v1/glossary

List glossary terms with pagination and filtering.

```bash
curl "http://localhost:8000/api/v1/glossary?kb_id=itops&limit=50"
```

#### GET /api/v1/glossary/domain-stats

Get term counts by domain.

#### GET /api/v1/glossary/source-stats

Get term counts by source.

#### GET /api/v1/glossary/similarity-distribution

Get similarity score distribution.

#### GET /api/v1/glossary/discovered-synonyms

Get automatically discovered synonyms.

#### POST /api/v1/glossary/batch-approve

Batch approve pending terms.

#### POST /api/v1/glossary/batch-delete

Batch delete terms.

---

### Ownership

#### GET /api/v1/admin/ownership/documents

List document ownership assignments.

#### POST /api/v1/admin/ownership/documents

Assign document owner.

#### POST /api/v1/admin/ownership/documents/{document_id}/transfer

Transfer document ownership.

#### GET /api/v1/admin/ownership/stale

Get stale (unverified) document owners.

#### GET /api/v1/admin/ownership/topics

List topic ownership assignments.

#### GET /api/v1/knowledge/ownership/search

Search for document owners by query (expert finder).

---

### KB Admin (Extended)

#### GET /api/v1/admin/kbs

List all KBs with admin details.

#### GET /api/v1/admin/kbs/stats

Get aggregate KB statistics.

#### GET /api/v1/admin/kbs/{kb_id}/stats

Get detailed stats for a KB (document count, chunk count, quality distribution).

#### GET /api/v1/admin/kbs/{kb_id}/documents

List documents in a KB.

#### GET /api/v1/admin/kbs/{kb_id}/categories

Get document categories.

#### GET /api/v1/admin/kbs/{kb_id}/trust-scores

Get trust scores for KB documents.

#### GET /api/v1/admin/kbs/{kb_id}/trust-scores/distribution

Get trust score distribution.

#### GET /api/v1/admin/kbs/{kb_id}/lifecycle

Get document lifecycle status.

#### GET /api/v1/admin/kbs/{kb_id}/coverage-gaps

Identify coverage gaps in a KB.

#### GET /api/v1/admin/kbs/{kb_id}/freshness

Get document freshness analysis.

#### GET /api/v1/admin/kbs/{kb_id}/members

List KB members.

#### POST /api/v1/admin/kbs/{kb_id}/members

Add a KB member.

#### DELETE /api/v1/admin/kbs/{kb_id}/members/{member_id}

Remove a KB member.

#### POST /api/v1/admin/kbs/search-cache/clear

Clear search cache for a KB.

---

### Graph Admin (Extended)

#### POST /api/v1/admin/graph/search

Search graph entities.

#### GET /api/v1/admin/graph/experts

Find experts by topic.

#### POST /api/v1/admin/graph/expand

Expand graph from a node (multi-hop).

#### POST /api/v1/admin/graph/integrity/check

Check graph integrity.

#### POST /api/v1/admin/graph/integrity/run

Run graph integrity repair.

#### GET /api/v1/admin/graph/integrity

Get integrity check results.

#### POST /api/v1/admin/graph/path

Find path between two entities.

#### GET /api/v1/admin/graph/communities

List graph communities.

#### POST /api/v1/admin/graph/impact

Analyze impact of an entity change.

#### GET /api/v1/admin/graph/health

Get graph database health.

#### POST /api/v1/admin/graph/timeline

Get entity timeline.

---

### Jobs

#### GET /api/v1/jobs

List all async jobs.

#### GET /api/v1/jobs/{job_id}

Get job details.

#### POST /api/v1/jobs/{job_id}/cancel

Cancel a running job.

---

### Feedback

#### POST /api/v1/knowledge/feedback

Submit feedback on a search result or RAG answer.

#### GET /api/v1/admin/feedback/list

List all feedback entries (admin).

#### GET /api/v1/admin/feedback/stats

Get feedback statistics.

---

## Error Codes

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 400 | Bad request (invalid parameters, missing directory) |
| 401 | Unauthorized (auth enabled, no valid token) |
| 403 | Forbidden (insufficient permissions) |
| 404 | Resource not found |
| 500 | Internal server error |
| 503 | Service unavailable (backend not initialized) |

Error response format:

```json
{
  "detail": "Descriptive error message"
}
```

## Rate Limits

No built-in rate limiting. Use an API gateway (e.g., nginx, Kong) or K8s Ingress for production rate limiting.
