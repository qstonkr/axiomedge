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

## Edge Server Deployment

매장 엣지 서버에 LLM 추론 서버를 배포합니다. Docker 없이 단일 바이너리로 동작.

### 설치

```bash
# Linux / macOS
curl -sL https://s3.../install.sh | \
  STORE_ID=gangnam-01 \
  EDGE_API_KEY=your-key \
  MANIFEST_URL=https://s3.../pbu-store/manifest.json \
  CENTRAL_API_URL=https://knowledge-api.gs.internal \
  bash
```

```powershell
# Windows (PowerShell)
$env:STORE_ID="gangnam-01"
$env:EDGE_API_KEY="your-key"
$env:MANIFEST_URL="https://s3.../manifest.json"
$env:CENTRAL_API_URL="https://knowledge-api.gs.internal"
irm https://s3.../install.ps1 | iex
```

### 서비스 구조

| OS | 추론 서버 | 주기적 sync (5분) |
|------|----------|------------|
| Linux | systemd `edge-server.service` | `edge-sync.timer` |
| Windows | nssm Windows Service | Task Scheduler |
| macOS | launchd plist | launchd plist |

### Heartbeat

엣지 서버는 5분마다 중앙 서버에 상태를 push합니다:
- `POST /api/v1/distill/edge-servers/heartbeat` (Bearer 인증)
- 응답에 `pending_model_update` / `pending_app_update` 플래그 포함
- 플래그가 true이면 즉시 업데이트 수행

### 앱 업데이트

1. `sync.py`가 manifest에서 새 앱 버전 감지 → staging에 다운로드
2. `update-edge.sh/ps1`이 서비스 중지 → 바이너리 교체 → 시작 → 헬스체크
3. 헬스체크 실패 시 자동 롤백

### 최소 사양

| 모델 | GGUF | 최소 RAM | CPU |
|------|------|---------|-----|
| Qwen2.5-0.5B | 379MB | 2GB | 2코어 |
| Gemma3-1B | 778MB | 2GB | 2코어 |
| EXAONE-2.4B | 1.5GB | 4GB | 4코어 |

---

## 대량 업로드 (Bulk Upload — Presigned URL Flow)

수천 개 파일 / 수십 GB 같은 대량 업로드는 **사용자 브라우저 → S3/MinIO 직접
PUT** 패턴 사용 — 백엔드는 metadata + arq job 등록만 담당. API 프로세스
RAM/CPU 0 부담.

**자동 분기**: `DocumentUploader.tsx` 가 파일 개수 ≥ 5 또는 누적 사이즈 ≥
100 MB 면 bulk flow, 그 미만은 기존 multipart endpoint 사용 (1 round-trip
이 더 빠름).

**필요 인프라**: S3 호환 object storage (MinIO 또는 AWS S3).

### MinIO (on-prem)

`docker-compose.yml` 에 이미 포함 — `docker compose up -d minio`.

**환경변수**:
```bash
AWS_S3_ENDPOINT_URL=http://localhost:9000   # docker net 안: http://minio:9000
UPLOADS_S3_BUCKET=axiomedge-uploads
UPLOADS_S3_PREFIX=uploads/
UPLOADS_S3_URL_TTL=3600                     # presigned URL 유효 1h
AWS_ACCESS_KEY_ID=minioadmin                # MINIO_ROOT_USER 와 동일
AWS_SECRET_ACCESS_KEY=minioadmin            # MINIO_ROOT_PASSWORD 와 동일
```

**bucket 생성 (first-run)**:
```bash
docker exec axiomedge-minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker exec axiomedge-minio mc mb local/axiomedge-uploads
```

또는 MinIO Console (http://localhost:9001) 에서 생성.

### AWS S3 (cloud)

```bash
# AWS_S3_ENDPOINT_URL 미설정 → AWS 표준 endpoint
UPLOADS_S3_BUCKET=your-org-uploads
UPLOADS_S3_PREFIX=axiomedge-uploads/
AWS_REGION=ap-northeast-2
AWS_PROFILE=your-profile     # 또는 AWS_ACCESS_KEY_ID/SECRET
```

**CORS 설정 필수** (브라우저가 직접 PUT 하므로):
```json
[
  {
    "AllowedMethods": ["PUT"],
    "AllowedOrigins": ["https://your-frontend.example.com"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3000
  }
]
```

### Migration

```bash
uv run alembic upgrade head    # 0009_bulk_upload_sessions 적용
```

### Worker

bulk upload arq job (`ingest_from_object_storage`) 실행을 위해 worker 떠있어야:
```bash
uv run arq src.jobs.worker.WorkerSettings
```

worker 안 떠있어도 init/finalize 는 동작 (DB row 만 생성) — 사용자가 worker
시작 시 자동 진행. presigned URL 1시간 유효 안에 업로드 + finalize 완료 필요.

### Failure 시나리오

- **사용자가 init 후 abort** → S3 orphan object → cleanup cron (별도, 24h 후
  unfinalized session 의 S3 prefix 삭제) 권장. 현재는 운영자 수동 정리.
- **arq worker crash** → arq retry (max_tries=3). 같은 파일 두 번 ingest 안
  됨 — `RawDocument.sha256(s3_key)` 로 doc_id 계산하고 ingestion pipeline 의
  content_hash dedup 가 차단.
- **partial failure** — 일부 파일 ingest 실패 시 `bulk_upload_sessions.errors`
  JSON 에 누적 + status="failed". 사용자가 status 폴링으로 확인.

---

## 파일 업로드 한도 (5GB)

`config/weights/pipeline.py:48` 의 `max_file_size_mb=5120` (5GB) — 사용자
인제스션 (file_upload connector + KB 직접 업로드) 의 파일당 한도.

**Frontend** (`DocumentUploader.tsx`): client-side 5GB pre-check 로 wasted
upload 차단. 한도 초과 파일은 toast warning + skip.

**Backend** (`ingest.py:upload_file`): streaming upload — 1MB chunk 씩 tempfile
write. constant 메모리 사용 (전체 파일 RAM 로드 X). 한도 초과 시 즉시 413.

**Reverse proxy / ingress** 가 5GB 까지 통과시키도록 별도 설정 필수:

| Ingress | 설정 |
|---|---|
| nginx-ingress (k8s) | `nginx.ingress.kubernetes.io/proxy-body-size: "5g"` + read/send timeout 600s + `proxy-request-buffering: "off"` (sample: `deploy/k8s/api/ingress.example.yaml`) |
| Traefik | middleware buffering `maxRequestBodyBytes: 5368709120` |
| nginx (bare) | `client_max_body_size 5G;` + `client_body_timeout 600s;` |
| AWS ALB | listener attribute — body size 자체는 unlimited 이나 idle timeout 600s 권장 |
| GCP GLB | BackendConfig `timeoutSec: 600` |

**값 변경 시 sync 위치 3곳**:
1. `src/config/weights/pipeline.py:48` `max_file_size_mb`
2. `src/apps/web/src/components/my-knowledge/DocumentUploader.tsx` `MAX_FILE_SIZE_BYTES`
3. ingress / reverse proxy `client_max_body_size`

⚠️ **운영 부담**: 동시 업로드 N건 시 N × 5GB tempfile 디스크 사용. 1MB chunk
streaming 이라 RAM 은 안전 (chunk × concurrent ≈ 100 MB), 디스크는 별도 모니터링.
