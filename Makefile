.PHONY: setup setup-distill-toolchain start stop api dashboard crawl ingest search test test-unit test-unit-fast test-integration test-e2e test-coverage-gate tei-refresh

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
	@./scripts/setup_distill_toolchain.sh

# === Infrastructure ===
start:
	docker compose -f deploy/docker-compose.yml up -d
	@echo "Qdrant: http://localhost:6333"
	@echo "Neo4j:  http://localhost:7474"
	@echo "Ollama: http://localhost:11434"

stop:
	docker compose -f deploy/docker-compose.yml down

# === Services ===
api: tei-refresh
	uv run uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers 2 --timeout-keep-alive 300

# Sync current egress IP to the TEI/PaddleOCR security group (jbkim-auto-* rules).
# Idempotent — safe to run before any service that needs BGE/Reranker/PaddleOCR.
tei-refresh:
	@uv run python scripts/refresh_tei_access.py

dashboard:
	uv run streamlit run src/apps/dashboard/app.py --server.address 0.0.0.0 --server.port 8501

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

k8s-deploy:
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
	uv run python scripts/coverage_gate.py --base origin/main --threshold 80

test-integration:
	uv run pytest tests/integration/ -v --no-cov

test-e2e:
	uv run pytest tests/e2e/ -v --no-cov -m e2e

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
