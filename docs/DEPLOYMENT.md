# Deployment Guide

## Prerequisites

- Kubernetes cluster (k3s recommended for single-node) or Docker Compose for local
- `kubectl` configured with cluster access
- Docker (for building images)
- At least 8GB RAM, 4 CPU cores available to the cluster

## Quick Start

```bash
# 1. Build the API image
make docker-build

# 2. Deploy all services to K8s
make k8s-deploy

# 3. Verify
kubectl -n knowledge get pods
```

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         knowledge namespace      │
                    │                                  │
  Users ──────────▶│  Dashboard (:30501)               │
                    │      │                           │
                    │      ▼                           │
                    │  API Server (:8000)  ◀── HPA     │
                    │      │                           │
                    │  ┌───┴───────────────────────┐   │
                    │  │   │        │       │      │   │
                    │  ▼   ▼        ▼       ▼      ▼   │
                    │ Qdrant Neo4j Postgres Redis  TEI │
                    │         │                    │   │
                    │      Ollama    PaddleOCR Reranker│
                    └─────────────────────────────────┘
```

All resources deploy into the `knowledge` namespace. The API server connects to backing services via internal DNS (`<service>.knowledge.svc`).

## K8s Deployment

### 1. Install k3s (single-node)

```bash
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

### 2. Build and load the API image

```bash
make docker-build
# For k3s, import directly:
sudo k3s ctr images import <(docker save knowledge-local:latest)
```

### 3. Configure secrets

Edit `k8s/postgres/secret.yaml` with production credentials:

```yaml
stringData:
  POSTGRES_USER: <user>
  POSTGRES_PASSWORD: <password>
  DATABASE_URL: postgresql+asyncpg://<user>:<password>@postgres.knowledge.svc:5432/knowledge_db
```

### 4. Deploy

```bash
kubectl apply -k k8s/
```

This deploys all components via Kustomize:

| Component | Type | Resources |
|-----------|------|-----------|
| PostgreSQL | StatefulSet | 256Mi-512Mi RAM, 5Gi PVC |
| Qdrant | Deployment | Vector database |
| Neo4j | Deployment | Graph database |
| Ollama | Deployment | LLM (EXAONE 3.5 7.8B) |
| Redis | Deployment | Cache layer |
| PaddleOCR | Deployment | OCR service |
| TEI (BGE-M3) | Deployment | Embedding server |
| TEI (Reranker) | Deployment | Cross-encoder reranker |
| API | Deployment + HPA | 1-4 replicas, autoscales at 70% CPU |
| Dashboard | Deployment | Streamlit UI |
| Crawler | Deployment | Web crawler |

### 5. Wait for readiness

```bash
kubectl -n knowledge wait --for=condition=ready pod -l app=qdrant --timeout=120s
kubectl -n knowledge wait --for=condition=ready pod -l app=knowledge-api --timeout=120s
```

## Environment Variables

Core variables configured in the API deployment (`k8s/api/deployment.yaml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (from secret) | PostgreSQL connection string |
| `QDRANT_URL` | `http://qdrant.knowledge.svc:6333` | Qdrant vector DB |
| `NEO4J_URI` | `bolt://neo4j.knowledge.svc:7687` | Neo4j graph DB |
| `NEO4J_AUTH` | `none` | Neo4j auth (set for production) |
| `OLLAMA_BASE_URL` | `http://ollama.knowledge.svc:11434` | Ollama LLM server |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | LLM model name |
| `REDIS_URL` | `redis://redis.knowledge.svc:6379` | Redis cache |
| `PADDLEOCR_API_URL` | `http://paddleocr.knowledge.svc:8866/ocr` | PaddleOCR API |
| `BGE_TEI_URL` | `http://bge-m3.knowledge.svc:80` | TEI embedding server |
| `RERANKER_TEI_URL` | `http://bge-reranker.knowledge.svc:8081` | TEI reranker server |
| `USE_CLOUD_EMBEDDING` | `true` | Use TEI for embeddings (vs local ONNX) |
| `USE_SAGEMAKER_LLM` | `false` | Use SageMaker instead of Ollama |
| `AUTH_ENABLED` | `false` | Enable authentication |
| `AUTH_PROVIDER` | `local` | Auth provider: `local`, `internal`, `keycloak`, `azure_ad` |
| `AUTH_JWT_SECRET` | (required if auth enabled) | JWT signing secret |

See `.env.example` for the complete list.

## Health Check Verification

```bash
# API health (checks Qdrant, Neo4j, embedding, LLM, Redis, PostgreSQL, PaddleOCR)
curl http://<api-host>:8000/health

# Expected response:
# {"status": "healthy", "checks": {"qdrant": true, "embedding": true, ...}}

# Metrics
curl http://<api-host>:8000/metrics

# Prometheus format
curl "http://<api-host>:8000/metrics?format=prometheus"

# K8s pod status
make k8s-status
```

The API deployment includes three probes:
- **Startup**: `/health` every 5s, up to 12 failures (60s max startup time)
- **Readiness**: `/health` every 10s
- **Liveness**: `/health` every 30s

## Troubleshooting

### Pods stuck in Pending

```bash
kubectl -n knowledge describe pod <pod-name>
# Common cause: insufficient resources or PVC not bound
kubectl -n knowledge get pvc
```

### API returns 503

The API starts in degraded mode if backends are not ready. Check which services failed:

```bash
curl http://<api-host>:8000/health
# Check individual service logs:
kubectl -n knowledge logs deploy/knowledge-api
kubectl -n knowledge logs deploy/qdrant
```

### Ollama model not loaded

```bash
kubectl -n knowledge exec deploy/ollama -- ollama list
# If model missing:
kubectl -n knowledge exec deploy/ollama -- ollama pull exaone3.5:7.8b
```

### PaddleOCR crashes

PaddleOCR container restarts are expected occasionally (see CLAUDE.md for PaddlePaddle build-from-source requirement). The container has `restart: unless-stopped` policy. Check:

```bash
kubectl -n knowledge logs deploy/paddleocr --previous
```

### Database migration issues

```bash
kubectl -n knowledge exec deploy/knowledge-api -- python -c "from src.database.init_db import init_database; import asyncio; asyncio.run(init_database('...'))"
```

## Scaling

### Horizontal Pod Autoscaler

The API deployment includes an HPA (`k8s/api/hpa.yaml`):
- Min replicas: 1
- Max replicas: 4
- Scale-up trigger: 70% CPU utilization
- Scale-up: +1 pod every 30s
- Scale-down: -1 pod every 60s (5min stabilization)

To adjust:

```bash
kubectl -n knowledge edit hpa knowledge-api
```

### Vertical scaling

Adjust resource limits in the deployment manifests:

```bash
kubectl -n knowledge set resources deploy/knowledge-api --limits=cpu=4,memory=8Gi
```

### Qdrant scaling

For large datasets, increase Qdrant resources and add persistent storage:

```bash
kubectl -n knowledge edit deploy/qdrant
```

## Backup / Restore

### PostgreSQL

```bash
# Backup
kubectl -n knowledge exec statefulset/postgres -- \
  pg_dump -U knowledge knowledge_db > backup.sql

# Restore
kubectl -n knowledge exec -i statefulset/postgres -- \
  psql -U knowledge knowledge_db < backup.sql
```

### Qdrant snapshots

```bash
# Create snapshot for a collection
curl -X POST "http://<qdrant-host>:6333/collections/<collection>/snapshots"

# List snapshots
curl "http://<qdrant-host>:6333/collections/<collection>/snapshots"

# Download snapshot
curl "http://<qdrant-host>:6333/collections/<collection>/snapshots/<snapshot-name>" -o snapshot.tar
```

### Neo4j

```bash
# Dump (stop neo4j first or use online backup with enterprise)
kubectl -n knowledge exec deploy/neo4j -- neo4j-admin database dump neo4j --to-path=/tmp/
kubectl -n knowledge cp knowledge/<neo4j-pod>:/tmp/neo4j.dump ./neo4j.dump
```

## Local Development (Docker Compose)

For local development without K8s:

```bash
make start      # Start infrastructure (Qdrant, Neo4j, PostgreSQL, Ollama, Redis, TEI, PaddleOCR)
make api        # Start API server on :8000
make dashboard  # Start Streamlit dashboard on :8501
make stop       # Stop infrastructure
```
