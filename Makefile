.PHONY: setup setup-distill-toolchain start stop api dashboard crawl ingest search test test-unit test-unit-fast test-integration test-e2e test-coverage-gate tei-refresh web-install web-dev web-build web-typecheck web-lint web-test web-test-e2e web-gen-api web-gen-api-offline web-docker-build web-docker-run

# Pin Compose project name so existing knowledge-local_* volumes are reused
# regardless of the working directory or compose file location.
export COMPOSE_PROJECT_NAME = knowledge-local

# === Setup ===
setup:
	uv sync
	@echo "Download BGE-M3 ONNX model:"
	@echo "  huggingface-cli download BAAI/bge-m3 --local-dir ./models/bge-m3"
	@echo "Pull EXAONE model:"
	@echo "  docker exec -it $$(docker ps -q -f name=ollama) ollama pull exaone3.5:7.8b"

# Build llama.cpp toolchain (convert_hf_to_gguf.py + llama-quantize + libllama)
# from a single matching commit. 파이썬 convert 스크립트와 C++ quantize/libllama
# 가 버전 드리프트 있으면 신규 아키텍처 (EXAONE, Kanana2 등) 가 깨진다.
# 업스트림 llama.cpp 에 새 아키텍처 지원 추가 시 재실행해서 갱신.
setup-distill-toolchain:
	@./scripts/ops/setup_distill_toolchain.sh

# === Infrastructure ===
# IMPORTANT: -p knowledge-local 명시 — 이 prefix 가 없으면 docker compose 가
# cwd (또는 -f 경로의 dir) 기반으로 project 명을 자동 추정해 새 빈 볼륨이
# 생성될 수 있다. 기존 데이터 볼륨은 ``knowledge-local_*`` 이름이므로 반드시
# 동일 project 명을 명시해 마운트되도록 한다.
start:
	docker compose -p knowledge-local -f deploy/docker-compose.yml up -d
	@echo "Qdrant: http://localhost:6333"
	@echo "Neo4j:  http://localhost:7474"
	@echo "Ollama: http://localhost:11434"

stop:
	docker compose -p knowledge-local -f deploy/docker-compose.yml down

# === Services ===
# `tei-refresh` 는 AWS 사용 시에만 필요. AWS 접근 가능한 환경이면 수동 호출:
#   make tei-refresh && make api
api:
	# Dev default: --workers 1. Multi-worker (--workers ≥ 2) currently causes
	# httpx.ReadTimeout when several workers contend for the same local Ollama
	# instance — local 7.8b model is the bottleneck, not the API. Revisit after
	# Ollama capacity is increased (cloud TEI/SageMaker, larger box, etc.).
	# Production deploy uses gunicorn from deploy/k8s with its own worker count.
	uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-keep-alive 300

api-multi:
	# Opt-in multi-worker. Use only when Ollama (or USE_SAGEMAKER_LLM=true) can
	# absorb concurrent inference; otherwise workers timeout each other out.
	uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers 2 --timeout-keep-alive 300

# Sync current egress IP to the TEI/PaddleOCR security group (jbkim-auto-* rules).
# AWS 자격증명 필요 — 로컬 dev (Ollama+ONNX) 에서는 호출 불필요.
tei-refresh:
	@uv run python scripts/refresh_tei_access.py

dashboard:
	uv run streamlit run src/apps/dashboard/app.py --server.address 0.0.0.0 --server.port 8501

# === Web (Next.js — src/apps/web) ===
WEB_DIR = src/apps/web

web-install:
	pnpm --dir $(WEB_DIR) install

web-dev:
	pnpm --dir $(WEB_DIR) dev

web-build:
	pnpm --dir $(WEB_DIR) build

web-typecheck:
	pnpm --dir $(WEB_DIR) typecheck

web-lint:
	pnpm --dir $(WEB_DIR) lint

web-test:
	pnpm --dir $(WEB_DIR) test

# Playwright E2E. Boots `next dev` automatically unless PLAYWRIGHT_BASE_URL is set.
web-test-e2e:
	pnpm --dir $(WEB_DIR) test:e2e

# Regenerate src/lib/api/types.ts from running FastAPI's /openapi.json.
# Requires `make api` running (or set API_OPENAPI_URL to a different host).
web-gen-api:
	pnpm --dir $(WEB_DIR) gen:api

# Same as web-gen-api but does not need uvicorn — uses scripts/dump_openapi.py
# which imports the FastAPI app in-process. Faster, no port collision.
web-gen-api-offline:
	pnpm --dir $(WEB_DIR) gen:api:offline

# Build production Docker image (multi-stage, standalone Next.js).
WEB_IMAGE ?= axiomedge-web:latest
web-docker-build:
	docker build -t $(WEB_IMAGE) -f $(WEB_DIR)/Dockerfile $(WEB_DIR)

# Run the built image. Override API_URL when FastAPI lives elsewhere.
web-docker-run:
	docker run --rm -it -p 3000:3000 \
	  -e API_URL=$${API_URL:-http://host.docker.internal:8000} \
	  $(WEB_IMAGE)

# === MCP Server ===
mcp:
	uv run python -m src.mcp_server

mcp-sse:
	uv run python -m src.mcp_server --sse

# === CLI ===
crawl:
	uv run python -m src.cli.crawl $(ARGS)

ingest: tei-refresh
	uv run python -m src.cli.ingest $(ARGS)

search:
	uv run python -m src.cli.search $(ARGS)

# === Docker Build ===
docker-build:
	docker build -f deploy/Dockerfile --target api -t knowledge-local:latest .

# === K8s (k3s + local-path) ===
k8s-install-k3s:
	@echo "Install k3s (single node):"
	@echo "  curl -sfL https://get.k3s.io | sh -"
	@echo "  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"

k8s-deploy:  ## Server-side dry-run (PR-8 M) — manifest validate only
	kubectl apply -k k8s/ --dry-run=server
	@echo "[dry-run] OK. To apply for real: make k8s-apply"

k8s-apply:  ## Apply manifests for real (PR-8 M)
	kubectl apply -k k8s/
	@echo ""
	@echo "Dashboard: http://localhost:30501"
	@echo "Waiting for pods..."
	kubectl -n knowledge wait --for=condition=ready pod -l app=qdrant --timeout=120s || true
	kubectl -n knowledge wait --for=condition=ready pod -l app=neo4j --timeout=120s || true
	kubectl -n knowledge get pods

k8s-status:
	kubectl -n knowledge get pods,svc,pvc

k8s-teardown:
	kubectl delete -k k8s/ --ignore-not-found

k8s-logs:
	kubectl -n knowledge logs -f deploy/knowledge-api

# === Quality ===
type-check:
	uv run pyright src/

lint-imports:
	uv run lint-imports

# === Tests ===
test:
	uv run pytest tests/ -v --no-cov

# Unit tests + coverage 측정 (PR 준비용). 전체 coverage 전역 floor 는
# pyproject.toml::[tool.coverage.report]::fail_under 에서 enforce.
# Touched-file 80% floor 는 `make test-coverage-gate` 로 확인.
# 상세: docs/TESTING.md
test-unit:
	uv run pytest tests/unit/ \
		--ignore=tests/unit/test_jobs.py \
		$(shell ls tests/unit/test_dashboard_*.py 2>/dev/null | sed 's/^/--ignore=/') \
		--cov=src \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-report=json:coverage.json \
		--cov-fail-under=76 \
		-q
	@PYTHONPATH=src/apps/dashboard uv run pytest tests/unit/test_dashboard_*.py -q --no-cov 2>/dev/null || true

# 빠른 iteration 용 — coverage 측정 skip.
test-unit-fast:
	uv run pytest tests/unit/ --ignore=tests/unit/test_jobs.py $(shell ls tests/unit/test_dashboard_*.py 2>/dev/null | sed 's/^/--ignore=/') -q --no-cov
	@PYTHONPATH=src/apps/dashboard uv run pytest tests/unit/test_dashboard_*.py -q --no-cov 2>/dev/null || true

# PR 이 수정한 src/*.py 파일 각각 80% floor 검사. test-unit 이 먼저 실행돼
# coverage.json 을 생성해야 한다.
test-coverage-gate:
	uv run python scripts/ops/coverage_gate.py --base origin/main --threshold 80

test-integration:
	uv run pytest tests/integration/ -v --no-cov

test-e2e:
	uv run pytest tests/e2e/ -v --no-cov -m e2e

# PR-13 (K) — ingest pipeline 성능 회귀 가드 (nightly)
perf-ingest:
	@echo "[PR-13] Running ingest performance regression suite..."
	uv run pytest tests/integration/test_pipeline_*.py -v --no-cov -m perf || \
		(echo "FAIL: perf regression detected"; exit 1)

# === DB Schema Migrations (Alembic) ===
db-init:
	uv run python -m src.stores.postgres.init_db

db-upgrade:
	uv run alembic upgrade head

db-revision:
	@if [ -z "$(MSG)" ]; then echo "Usage: make db-revision MSG='add user_preferences'"; exit 1; fi
	uv run alembic revision --autogenerate -m "$(MSG)"

db-history:
	uv run alembic history --verbose

db-current:
	uv run alembic current

# P2-1 — single-step downgrade (most common during dev). Backup before use!
db-downgrade-1:
	@echo "[db-downgrade] Backup recommended: make backup-pg"
	@echo "[db-downgrade] Stepping back 1 revision..."
	uv run alembic downgrade -1

# Downgrade to a specific revision (e.g. ``make db-downgrade-to REV=0009_bulk_upload_sessions``)
db-downgrade-to:
	@if [ -z "$(REV)" ]; then echo "Usage: make db-downgrade-to REV='0009_bulk_upload_sessions'"; exit 1; fi
	@echo "[db-downgrade] Backup recommended: make backup-pg"
	uv run alembic downgrade $(REV)

# Diagnose alembic_version vs schema mismatch (P1-3 / production handover use)
db-stamp:
	@if [ -z "$(REV)" ]; then echo "Usage: make db-stamp REV='0009_bulk_upload_sessions'"; exit 1; fi
	@echo "[db-stamp] Forcing alembic_version to $(REV) WITHOUT applying migrations."
	@echo "[db-stamp] Use only when DB schema is known to match REV manually."
	uv run alembic stamp $(REV)

# === Backups ===
backup-pg:
	./scripts/ops/backup_db.sh

backup-qdrant:
	./scripts/ops/backup_qdrant.sh

backup-neo4j:
	./scripts/ops/backup_neo4j.sh

backup-all: backup-pg backup-qdrant backup-neo4j

# === Secret Management (SOPS + age) ===
# Setup: docs/SECRETS.md
# Requires: brew install age sops + ~/.config/sops/age/keys.txt

secrets-encrypt:
	@if [ ! -f .env ]; then echo "ERROR: .env not found"; exit 1; fi
	sops -e .env > .env.encrypted
	@echo "✓ Encrypted: .env → .env.encrypted"
	@echo "  Commit .env.encrypted; .env stays in .gitignore"

secrets-decrypt:
	@if [ ! -f .env.encrypted ]; then echo "ERROR: .env.encrypted not found"; exit 1; fi
	sops -d .env.encrypted > .env
	@echo "✓ Decrypted: .env.encrypted → .env (local only — do not commit)"

secrets-check:
	@if [ ! -f .env.encrypted ]; then echo "no .env.encrypted to check"; exit 0; fi
	@sops -d .env.encrypted > /dev/null && echo "✓ .env.encrypted is valid SOPS file"

secrets-updatekeys:
	sops updatekeys .env.encrypted
	@echo "✓ .sops.yaml recipients re-applied to .env.encrypted"

# === Search quality evaluation ===
eval-quality:
	uv run python scripts/eval_quality_gate.py --baseline eval/baseline.json

eval-update-baseline:
	@if [ ! -f eval/last_run.json ]; then echo "ERROR: run 'make eval-quality' first to generate eval/last_run.json"; exit 1; fi
	cp eval/last_run.json eval/baseline.json
	@echo "✓ Baseline updated from eval/last_run.json"

db-check:
	@if [ -z "$(FILE)" ]; then echo "Usage: make db-check FILE=migrations/versions/XXXX_YY.py"; exit 1; fi
	uv run python scripts/db_migration_check.py $(FILE)

# === Background Job Worker (Arq) ===
worker:
	uv run arq src.jobs.worker.WorkerSettings

backup-drill:
	DRILL_CONFIRM=yes ./scripts/ops/backup_drill.sh

# === Performance / Load Tests (k6) ===
perf-search:
	mkdir -p loadtest/results
	k6 run --summary-export=loadtest/results/search.json loadtest/search.js
	uv run python scripts/perf_check.py loadtest/results/search.json --scenario search.js

perf-health:
	mkdir -p loadtest/results
	k6 run --summary-export=loadtest/results/health.json loadtest/health.js
	uv run python scripts/perf_check.py loadtest/results/health.json --scenario health.js

perf-update-baseline:
	@if [ -z "$(SCENARIO)" ]; then echo "Usage: make perf-update-baseline SCENARIO=search.js"; exit 1; fi
	@uv run python -c "import json,sys; b=json.load(open('loadtest/baseline.json')); s=json.load(open('loadtest/results/$(SCENARIO).json'));\
import importlib.util; spec=importlib.util.spec_from_file_location('p','scripts/perf_check.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m);\
b['$(SCENARIO)'].update(m.extract_metrics(s)); open('loadtest/baseline.json','w').write(json.dumps(b, indent=2))"
	@echo "✓ Baseline updated for $(SCENARIO) from loadtest/results/$(SCENARIO).json"

# === Graph schema operator commands (Phase 5) ===
graph-schema-scaffold:
	@uv run python -m src.cli.graph_schema_cli scaffold $(source)

graph-schema-dry-run:
	@uv run python -m src.cli.graph_schema_cli dry-run $(kb)
