"""RAG 품질 검증

20개 샘플 문서 + 20개 쿼리로 검색율/응답율을 검증합니다.
테스트(test_rag_quality_evaluation.py)와 동일한 시나리오를
대시보드 UI에서 실행합니다.

Two Modes:
    1. Mock Mode (기본): 백엔드 없이 키워드 기반 검색 시뮬레이션
    2. Live Mode (OREO_API_TOKEN 설정): 실제 백엔드 API 호출

Created: 2026-02-20
"""

import time

import streamlit as st

st.set_page_config(page_title="RAG 품질 검증", page_icon="🎯", layout="wide")


import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from components.sidebar import render_sidebar
from components.metric_cards import render_quality_metrics
from services import api_client
from services.api_client import api_failed
# Local mode: no OREO_API_TOKEN needed
OREO_API_TOKEN = ""

render_sidebar(show_admin=True)


# ---------------------------------------------------------------------------
# 20 Sample Documents
# ---------------------------------------------------------------------------
SAMPLE_DOCUMENTS = [
    {
        "title": "K8s Pod Restart Guide",
        "content": (
            "Kubernetes Pod 재시작 방법\n\n"
            "1. kubectl rollout restart deployment/<name> -n <namespace>\n"
            "2. Pod가 정상적으로 재시작되었는지 확인: kubectl get pods -n <namespace>\n"
            "3. 로그 확인: kubectl logs -f deployment/<name> -n <namespace>\n\n"
            "주의사항: PDB(PodDisruptionBudget)가 설정되어 있으면 "
            "동시에 모든 Pod가 종료되지 않도록 보장됩니다."
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "ArgoCD Sync Guide",
        "content": (
            "ArgoCD 동기화 가이드\n\n"
            "ArgoCD는 GitOps 기반 지속적 배포(CD) 도구입니다.\n"
            "애플리케이션 동기화 절차:\n"
            "1. ArgoCD UI에서 Sync 버튼 클릭\n"
            "2. Revision 확인 후 동기화 실행\n"
            "3. Health Status가 Healthy로 변경될 때까지 대기\n\n"
            "자동 동기화 설정: argocd app set <app> --sync-policy automated"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Docker Image Build",
        "content": (
            "Docker 이미지 빌드 가이드\n\n"
            "ARM64 플랫폼 빌드:\n"
            "docker buildx build --platform linux/arm64 -t myapp:v1.0 .\n\n"
            "Multi-stage 빌드 패턴:\n"
            "FROM python:3.12-slim AS builder\n"
            "COPY requirements.txt .\nRUN pip install -r requirements.txt"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Redis Cluster Setup",
        "content": (
            "Redis 클러스터 구성 가이드\n\n"
            "Redis 6 이상에서 클러스터 모드 설정:\n"
            "1. redis.conf에 cluster-enabled yes 추가\n"
            "2. 최소 6개 노드 (3 master + 3 replica) 필요\n"
            "3. redis-cli --cluster create 명령어로 클러스터 생성"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "PostgreSQL Backup Strategy",
        "content": (
            "PostgreSQL 백업 전략\n\n"
            "1. 논리적 백업 (pg_dump):\n"
            "   pg_dump -Fc -f backup.dump dbname\n"
            "2. 물리적 백업 (pg_basebackup):\n"
            "   pg_basebackup -D /backup/data -Fp -Xs -P\n"
            "복원: pg_restore -d dbname backup.dump"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Datadog APM 설정 가이드",
        "content": (
            "Datadog APM 설정 방법\n\n"
            "DD_LLMOBS_ENABLED=true, DD_LLMOBS_ML_APP=oreo-agents\n"
            "patch_all(asyncio=False) 호출 (BaseHTTPMiddleware 충돌 방지)\n"
            "LLM Observability 메뉴에서 워크플로우 시각화"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Temporal Workflow 개발 가이드",
        "content": (
            "Temporal Workflow 개발\n\n"
            "Knowledge Sync Workflow:\n"
            "- kb-sync-miso-faq: 매일 오전 8시\n"
            "- kb-sync-jira-resolved: 매주 월요일\n"
            "cron_expression으로 주기 설정 가능"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Kong Gateway 라우팅",
        "content": (
            "Kong Gateway 라우팅 규칙\n\n"
            "/api/v1/auth/* → oreo-api (priority 200)\n"
            "/api/v1/mobile/* → oreo-agents (priority 180)\n"
            "Mobile API 확장 시 Kong 변경 없이 oreo-agents 내부 라우터만 추가"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Qdrant Vector DB 운영",
        "content": (
            "Qdrant Vector DB 운영 가이드\n\n"
            "Named vectors: bge_dense (1024d Cosine) + bge_sparse (Dot)\n"
            "RRF Fusion: α=0.4 dense + β=0.3 sparse + γ=0.3 ColBERT"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "MISO 포털 FAQ",
        "content": (
            "MISO AI 포털 FAQ\n\n"
            "Q: MISO란 무엇인가요?\n"
            "A: MISO는 GS리테일의 사내 AI 포털입니다 (Dify 기반).\n"
            "Q: 토큰 사용량은 어디서 확인하나요?\n"
            "A: LiteLLM SpendLogs 대시보드에서 확인합니다."
        ),
        "kb_id": "kb-miso",
        "tier": "GLOBAL",
    },
    {
        "title": "SLA 정책 문서",
        "content": (
            "OREO 서비스 SLA 정책\n\n"
            "서비스 가용성 목표: 99.9% (월 기준)\n"
            "장애 대응 시간: P1 < 15분, P2 < 1시간, P3 < 4시간"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "보안 점검 체크리스트",
        "content": (
            "분기별 보안 점검 체크리스트\n\n"
            "JWT 만료 시간: 24시간\n"
            "OIDC 설정 확인\n"
            "K8s PSS(Pod Security Standards) 적용"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "장애 대응 매뉴얼",
        "content": (
            "OREO 장애 대응 매뉴얼\n\n"
            "2단계: 초기 대응 (15분 이내)\n"
            "장애 범위 확인: kubectl get pods -A\n"
            "롤백: kubectl rollout undo"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "ECR Pull-through Cache 가이드",
        "content": (
            "Docker Hub Rate Limit 우회를 위한 ECR Pull-through Cache\n\n"
            "aws ecr create-pull-through-cache-rule \\\n"
            "  --ecr-repository-prefix docker-hub \\\n"
            "  --upstream-registry-url registry-1.docker.io"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Knowledge Sync 스케줄 관리",
        "content": (
            "Knowledge Sync Temporal 스케줄\n\n"
            "kb-sync-miso-faq: 매일 08:00 UTC (17:00 KST)\n"
            "kb-sync-jira-resolved: 매주 월요일 02:00 UTC (11:00 KST)\n"
            "수동 트리거: make kb-sync-miso TYPE=faq"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Multi-tenant KB 아키텍처",
        "content": (
            "Multi-tenant KB 3-Tier 구조\n\n"
            "GLOBAL: 전사 공용 KB — 전체 읽기 가능\n"
            "BU: 사업부 KB — organization_id 기반 접근\n"
            "TEAM: 팀 전용 KB — department_id 기반 접근"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Deduplication Pipeline",
        "content": (
            "4-Stage 중복 제거 파이프라인\n\n"
            "Stage 1: Bloom Filter (<1ms)\n"
            "Stage 2: MinHash LSH — Jaccard 유사도 ≥ 0.80\n"
            "Stage 3: SemHash — Cosine 유사도 ≥ 0.90\n"
            "Stage 4: LLM Conflict Detection (~100ms)"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Feature Flag 관리 정책",
        "content": (
            "Feature Flag 관리 정책\n\n"
            "기본 원칙: 모든 feature flag는 default=true (enabled)\n"
            "비활성화: 환경변수를 'false'로 명시 설정\n"
            "SSOT: apps/oreo-agents/src/core/feature_flags.py (48개 플래그)"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "Embedding 듀얼 아키텍처",
        "content": (
            "Embedding 듀얼 아키텍처\n\n"
            "Primary: BGE-M3 — 1024차원 dense + sparse vectors\n"
            "Cloud Fallback: Cohere Embed v4 — Matryoshka 256/512/1024/1536\n"
            "Ollama Fallback: bge-m3:latest"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
    {
        "title": "KTS (Knowledge Trust Score)",
        "content": (
            "KTS 6-Signal 가중 평균\n\n"
            "KTS = 0.20×SourceCredibility + 0.20×Freshness + 0.25×UserValidation\n"
            "    + 0.10×Usage + 0.15×Hallucination + 0.10×Consistency\n"
            "ConfidenceTier: HIGH(85+), MEDIUM(70-84), LOW(50-69), UNCERTAIN(<50)"
        ),
        "kb_id": "kb-infra",
        "tier": "GLOBAL",
    },
]


# ---------------------------------------------------------------------------
# 20 Evaluation Queries
# ---------------------------------------------------------------------------
EVALUATION_QUERIES = [
    {"query": "K8s Pod를 재시작하려면 어떻게 하나요?", "ground_truth": "kubectl rollout restart deployment/<name> -n <namespace> 명령어로 Pod를 재시작합니다.", "expected_doc": "K8s Pod Restart Guide"},
    {"query": "ArgoCD에서 애플리케이션을 동기화하는 방법은?", "ground_truth": "ArgoCD UI에서 Sync 버튼을 클릭하고, Revision 확인 후 동기화를 실행합니다.", "expected_doc": "ArgoCD Sync Guide"},
    {"query": "ARM64 플랫폼에서 Docker 이미지를 빌드하려면?", "ground_truth": "docker buildx build --platform linux/arm64 -t myapp:v1.0 . 명령어를 사용합니다.", "expected_doc": "Docker Image Build"},
    {"query": "Redis 클러스터 구성에 최소 몇 개의 노드가 필요한가요?", "ground_truth": "최소 6개 노드가 필요합니다 (3 master + 3 replica).", "expected_doc": "Redis Cluster Setup"},
    {"query": "PostgreSQL 논리적 백업 명령어는 무엇인가요?", "ground_truth": "pg_dump -Fc -f backup.dump dbname 명령어로 논리적 백업을 수행합니다.", "expected_doc": "PostgreSQL Backup Strategy"},
    {"query": "Datadog APM에서 LLM Observability를 활성화하는 환경변수는?", "ground_truth": "DD_LLMOBS_ENABLED=true, DD_LLMOBS_ML_APP=oreo-agents를 설정합니다.", "expected_doc": "Datadog APM 설정 가이드"},
    {"query": "Knowledge Sync FAQ 스케줄은 언제 실행되나요?", "ground_truth": "kb-sync-miso-faq는 매일 08:00 UTC (17:00 KST)에 실행됩니다.", "expected_doc": "Knowledge Sync 스케줄 관리"},
    {"query": "Kong Gateway에서 Mobile API 경로는 어떤 서비스로 라우팅되나요?", "ground_truth": "/api/v1/mobile/*는 oreo-agents로 라우팅됩니다 (priority 180).", "expected_doc": "Kong Gateway 라우팅"},
    {"query": "Qdrant에서 사용하는 RRF Fusion 가중치는?", "ground_truth": "RRF Fusion: α=0.4 dense + β=0.3 sparse + γ=0.3 ColBERT입니다.", "expected_doc": "Qdrant Vector DB 운영"},
    {"query": "MISO란 무엇이고, 토큰 사용량은 어디서 확인하나요?", "ground_truth": "MISO는 GS리테일의 사내 AI 포털(Dify 기반)입니다. 토큰 사용량은 LiteLLM SpendLogs 대시보드에서 확인합니다.", "expected_doc": "MISO 포털 FAQ"},
    {"query": "OREO 서비스의 SLA 가용성 목표는?", "ground_truth": "서비스 가용성 목표는 99.9% (월 기준)입니다.", "expected_doc": "SLA 정책 문서"},
    {"query": "보안 점검에서 JWT 만료 시간 기준은?", "ground_truth": "JWT 만료 시간은 24시간입니다.", "expected_doc": "보안 점검 체크리스트"},
    {"query": "장애 발생 시 초기 대응 시간은 얼마인가요?", "ground_truth": "초기 대응은 15분 이내에 수행합니다.", "expected_doc": "장애 대응 매뉴얼"},
    {"query": "ECR Pull-through Cache를 설정하는 명령어는?", "ground_truth": "aws ecr create-pull-through-cache-rule --ecr-repository-prefix docker-hub --upstream-registry-url registry-1.docker.io", "expected_doc": "ECR Pull-through Cache 가이드"},
    {"query": "Multi-tenant KB에서 BU 레벨 접근 제어 기준은?", "ground_truth": "BU 레벨은 organization_id 기반으로 접근을 제어합니다.", "expected_doc": "Multi-tenant KB 아키텍처"},
    {"query": "Dedup Pipeline Stage 2에서 사용하는 유사도 임계값은?", "ground_truth": "Stage 2는 MinHash LSH를 사용하며 Jaccard 유사도 ≥ 0.80이면 near-duplicate로 판정합니다.", "expected_doc": "Deduplication Pipeline"},
    {"query": "Feature Flag의 기본 정책은 무엇인가요?", "ground_truth": "모든 feature flag는 default=true (enabled)입니다.", "expected_doc": "Feature Flag 관리 정책"},
    {"query": "BGE-M3 임베딩 모델의 차원 수와 벡터 유형은?", "ground_truth": "BGE-M3는 1024차원 dense + sparse vectors를 생성합니다.", "expected_doc": "Embedding 듀얼 아키텍처"},
    {"query": "KTS에서 가장 높은 가중치를 가진 시그널은?", "ground_truth": "UserValidation이 0.25로 가장 높은 가중치를 가집니다.", "expected_doc": "KTS (Knowledge Trust Score)"},
    {"query": "Temporal Workflow에서 Knowledge Sync FAQ 크론 스케줄은?", "ground_truth": "kb-sync-miso-faq는 매일 오전 8시에 실행됩니다.", "expected_doc": "Temporal Workflow 개발 가이드"},
]


# ---------------------------------------------------------------------------
# 한국어 토큰화 유틸리티
# ---------------------------------------------------------------------------
_KO_SUFFIXES = (
    "에서는", "에서의", "에서도", "에서",
    "으로는", "으로",
    "에는", "에도",
    "이란", "인가요",
    "부터", "까지", "란",
    "를", "을", "은", "는", "이", "가", "의", "와", "과",
    "에", "도", "만", "로",
    "입니다", "합니다", "됩니다", "습니다",
)


def _tokenize(text: str) -> set[str]:
    """검색용 토큰화 (원본 + 어근)."""
    raw = text.lower().replace("?", "").replace(".", "").replace(",", "")
    words: set[str] = set()
    for w in raw.split():
        words.add(w)
        for suffix in sorted(_KO_SUFFIXES, key=len, reverse=True):
            if w.endswith(suffix) and len(w) > len(suffix):
                stem = w[: -len(suffix)]
                if len(stem) >= 2:
                    words.add(stem)
                break
    return words


def _stem_tokenize(text: str) -> set[str]:
    """정확도 비교용 토큰화 (어근만 추출)."""
    raw = text.lower()
    for ch in "?,;:!()[]{}+=\"'\u2014\u2013":
        raw = raw.replace(ch, " ")
    raw = raw.replace("\u00d7", " ")  # ×
    stems: set[str] = set()
    for w in raw.split():
        if not w:
            continue
        w = w.strip(".")
        if not w:
            continue
        stem = w
        for suffix in sorted(_KO_SUFFIXES, key=len, reverse=True):
            if w.endswith(suffix) and len(w) > len(suffix):
                candidate = w[: -len(suffix)]
                if len(candidate) >= 2:
                    stem = candidate
                break
        stems.add(stem)
    return stems


# ---------------------------------------------------------------------------
# Mock 검색 (백엔드 없이 로컬 시뮬레이션)
# ---------------------------------------------------------------------------
def _keyword_score(query: str, content: str) -> float:
    q_tokens = _tokenize(query)
    c_tokens = _tokenize(content + " " + content)
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


def _find_best_docs(query: str, top_k: int = 5) -> list[dict]:
    scored = []
    for doc in SAMPLE_DOCUMENTS:
        score = _keyword_score(query, doc["content"] + " " + doc["title"])
        scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "content": doc["content"],
            "document_name": doc["title"],
            "title": doc["title"],
            "kb_id": doc["kb_id"],
            "tier": doc["tier"],
            "score": round(score, 4),
            "trust_score": 0.85,
        }
        for score, doc in scored[:top_k]
        if score > 0.0
    ]


def _mock_search_answer(query: str) -> dict:
    """Mock hub_search_answer (백엔드 없이)."""
    sources = _find_best_docs(query, 5)
    answer = "\n".join(s["content"] for s in sources[:2]) if sources else ""
    confidence = "HIGH" if sources and sources[0]["score"] > 0.3 else "MEDIUM"
    return {
        "answer": answer,
        "sources": [
            {"title": s["title"], "tier": s["tier"], "trust_score": s["trust_score"], "rerank_score": s["score"]}
            for s in sources[:3]
        ],
        "confidence_level": confidence,
    }


# ---------------------------------------------------------------------------
# 응답 정확도 검증
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "합니다", "입니다", "있습니다", "됩니다", "사용합니다",
    "명령어로", "명령어를", "기반", "위한", "이상", "이하",
    "가장", "높은", "낮은", "같은", "다른", "모든",
    "가집니다", "갖습니다",
}


def _check_response_accuracy(answer: str, ground_truth: str) -> tuple[bool, float]:
    """ground truth 핵심 사실의 답변 포함 여부."""
    if not answer or not ground_truth:
        return False, 0.0
    gt_stems = _stem_tokenize(ground_truth)
    answer_stems = _stem_tokenize(answer)
    gt_meaningful = gt_stems - _STOPWORDS
    if not gt_meaningful:
        gt_meaningful = gt_stems
    overlap = len(gt_meaningful & answer_stems)
    score = overlap / len(gt_meaningful) if gt_meaningful else 0.0
    return score >= 0.50, score


# ---------------------------------------------------------------------------
# 평가 실행
# ---------------------------------------------------------------------------
def _run_evaluation(use_live: bool) -> list[dict]:
    """20개 쿼리 평가 실행. 결과 리스트 반환."""
    results = []
    progress = st.progress(0, text="평가 준비 중...")

    for i, q in enumerate(EVALUATION_QUERIES):
        progress.progress((i + 1) / len(EVALUATION_QUERIES), text=f"Q{i+1}/20: {q['query'][:30]}...")

        t0 = time.perf_counter()

        if use_live:
            raw = api_client.hub_search_answer(q["query"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if api_failed(raw):
                results.append({
                    "no": i + 1,
                    "query": q["query"],
                    "expected_doc": q["expected_doc"],
                    "ground_truth": q["ground_truth"],
                    "search_hit": False,
                    "top_doc": "-",
                    "answer": "",
                    "confidence": "ERROR",
                    "response_accurate": False,
                    "accuracy_score": 0.0,
                    "elapsed_ms": elapsed_ms,
                    "error": True,
                })
                continue
            answer = raw.get("answer", "")
            sources = raw.get("sources", [])
            confidence = raw.get("confidence_level", "")
            top_doc = sources[0].get("title", "-") if sources else "-"
            search_hit = any(
                q["expected_doc"].lower() in s.get("title", "").lower()
                for s in sources
            )
        else:
            raw = _mock_search_answer(q["query"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            answer = raw["answer"]
            sources = raw["sources"]
            confidence = raw["confidence_level"]
            top_doc = sources[0]["title"] if sources else "-"
            search_hit = any(
                q["expected_doc"].lower() in s.get("title", "").lower()
                for s in sources
            )

        accurate, score = _check_response_accuracy(answer, q["ground_truth"])
        results.append({
            "no": i + 1,
            "query": q["query"],
            "expected_doc": q["expected_doc"],
            "ground_truth": q["ground_truth"],
            "search_hit": search_hit,
            "top_doc": top_doc,
            "answer": answer[:200],
            "confidence": confidence,
            "response_accurate": accurate,
            "accuracy_score": score,
            "elapsed_ms": elapsed_ms,
            "error": False,
        })

    progress.empty()
    return results


# ---------------------------------------------------------------------------
# 결과 렌더링
# ---------------------------------------------------------------------------
def _render_results(results: list[dict]) -> None:
    """평가 결과 대시보드 렌더링."""
    total = len(results)
    errors = sum(1 for r in results if r.get("error"))
    valid = [r for r in results if not r.get("error")]
    valid_count = len(valid)

    search_hits = sum(1 for r in valid if r["search_hit"])
    response_hits = sum(1 for r in valid if r["response_accurate"])
    search_accuracy = search_hits / valid_count if valid_count else 0
    response_accuracy = response_hits / valid_count if valid_count else 0
    avg_score = sum(r["accuracy_score"] for r in valid) / valid_count if valid_count else 0
    avg_time = sum(r["elapsed_ms"] for r in valid) / valid_count if valid_count else 0

    # ── Quality Gate ──
    search_pass = search_accuracy >= 0.90
    response_pass = response_accuracy >= 0.90
    overall_pass = search_pass and response_pass

    gate_icon = "PASSED" if overall_pass else "FAILED"
    gate_color = "green" if overall_pass else "red"
    st.markdown(
        f"### :{gate_color}[Quality Gate: **{gate_icon}**]"
    )

    # ── 핵심 메트릭 ──
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("검색율", f"{search_accuracy:.0%}", help="올바른 문서 검색 비율")
    with c2:
        st.metric("응답율", f"{response_accuracy:.0%}", help="문맥 정확한 응답 비율")
    with c3:
        st.metric("평균 정확도", f"{avg_score:.3f}")
    with c4:
        st.metric("평균 응답시간", f"{avg_time:.0f}ms")
    with c5:
        st.metric("API 오류", f"{errors}건", delta_color="inverse")

    if errors:
        st.warning(f"{errors}건의 API 호출 실패가 있습니다.")

    st.markdown("---")

    # ── Per-query 상세 테이블 ──
    st.subheader("쿼리별 상세 결과")

    rows = []
    for r in results:
        s_icon = "O" if r["search_hit"] else ("ERR" if r.get("error") else "X")
        r_icon = "O" if r["response_accurate"] else ("ERR" if r.get("error") else "X")
        rows.append({
            "#": r["no"],
            "검색": s_icon,
            "응답": r_icon,
            "정확도": f"{r['accuracy_score']:.2f}",
            "신뢰도": r["confidence"],
            "시간(ms)": f"{r['elapsed_ms']:.0f}",
            "쿼리": r["query"][:45],
            "기대 문서": r["expected_doc"][:25],
            "검색 문서": r["top_doc"][:25],
        })

    df = pd.DataFrame(rows)

    # 색상 하이라이팅
    def _highlight(row):
        styles = [""] * len(row)
        if row["검색"] == "X":
            styles[1] = "background-color: #ffcccc"
        if row["응답"] == "X":
            styles[2] = "background-color: #ffcccc"
        if row["검색"] == "ERR" or row["응답"] == "ERR":
            styles[1] = "background-color: #ff9999"
            styles[2] = "background-color: #ff9999"
        return styles

    st.dataframe(
        df.style.apply(_highlight, axis=1),
        use_container_width=True,
        hide_index=True,
        height=min(len(rows) * 40 + 60, 860),
    )

    # ── 정확도 분포 차트 ──
    st.subheader("정확도 분포")
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        scores = [r["accuracy_score"] for r in valid]
        labels = [f"Q{r['no']}" for r in valid]
        fig_bar = px.bar(
            x=labels,
            y=scores,
            title="쿼리별 응답 정확도",
            labels={"x": "쿼리", "y": "정확도"},
            color=scores,
            color_continuous_scale=["#ff4444", "#ffaa00", "#44bb44"],
            range_color=[0, 1],
        )
        fig_bar.add_hline(y=0.50, line_dash="dash", line_color="red", annotation_text="Threshold (0.50)")
        fig_bar.update_layout(margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    with col_chart2:
        pass_count = sum(1 for s in scores if s >= 0.50)
        fail_count = len(scores) - pass_count
        fig_pie = px.pie(
            values=[pass_count, fail_count],
            names=["PASS", "FAIL"],
            title="응답 정확도 Pass/Fail",
            color_discrete_map={"PASS": "#44bb44", "FAIL": "#ff4444"},
        )
        fig_pie.update_layout(margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── MISS 상세 ──
    missed_search = [r for r in valid if not r["search_hit"]]
    missed_response = [r for r in valid if not r["response_accurate"]]

    if missed_search or missed_response:
        st.subheader("MISS 상세")
        if missed_search:
            st.markdown(f"**검색 MISS ({len(missed_search)}건)**")
            for r in missed_search:
                st.markdown(f"- Q{r['no']}: {r['query'][:50]} (기대: {r['expected_doc']}, 실제: {r['top_doc']})")
        if missed_response:
            st.markdown(f"**응답 MISS ({len(missed_response)}건)**")
            for r in missed_response:
                st.markdown(f"- Q{r['no']}: {r['query'][:50]} (정확도: {r['accuracy_score']:.2f})")

    # ── Ground Truth 비교 ──
    with st.expander("Ground Truth vs 응답 비교", expanded=False):
        for r in results:
            if r.get("error"):
                continue
            icon = "O" if r["response_accurate"] else "X"
            st.markdown(f"**Q{r['no']} [{icon}] {r['query'][:45]}**")
            st.caption(f"Ground Truth: {r['ground_truth']}")
            st.caption(f"응답 (앞 200자): {r['answer'][:200]}")
            st.markdown("---")


# ---------------------------------------------------------------------------
# 메인 UI
# ---------------------------------------------------------------------------
st.title("RAG 품질 검증")
st.caption("20개 샘플 문서 + 20개 쿼리로 검색율/응답율 검증 (테스트와 동일 시나리오)")

# 모드 표시
is_live = bool(OREO_API_TOKEN) and OREO_API_TOKEN != "your-token-here"
mode_label = "Live (실제 백엔드)" if is_live else "Mock (로컬 시뮬레이션)"
mode_color = "green" if is_live else "blue"
st.markdown(f"**모드**: :{mode_color}[{mode_label}]")

if is_live:
    st.info("OREO_API_TOKEN이 설정되어 있어 실제 백엔드(oreo-agents)를 호출합니다.")
else:
    st.info("Mock 모드: 백엔드 없이 키워드 기반 검색을 시뮬레이션합니다. Live 모드는 OREO_API_TOKEN 환경변수를 설정하세요.")

st.markdown("---")

# 샘플 문서 미리보기
with st.expander(f"샘플 문서 ({len(SAMPLE_DOCUMENTS)}건)", expanded=False):
    doc_rows = [
        {"#": i + 1, "제목": d["title"], "KB": d["kb_id"], "Tier": d["tier"], "길이": len(d["content"])}
        for i, d in enumerate(SAMPLE_DOCUMENTS)
    ]
    st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)

# 평가 쿼리 미리보기
with st.expander(f"평가 쿼리 ({len(EVALUATION_QUERIES)}건)", expanded=False):
    q_rows = [
        {"#": i + 1, "쿼리": q["query"], "기대 문서": q["expected_doc"], "Ground Truth": q["ground_truth"][:60]}
        for i, q in enumerate(EVALUATION_QUERIES)
    ]
    st.dataframe(pd.DataFrame(q_rows), use_container_width=True, hide_index=True)

st.markdown("---")

# 평가 실행 버튼
if st.button("평가 실행", type="primary", use_container_width=True):
    with st.spinner("20개 쿼리 평가 중..."):
        results = _run_evaluation(use_live=is_live)
    st.session_state["rag_eval_results"] = results

# 결과 표시 (세션에 저장된 결과가 있으면)
if "rag_eval_results" in st.session_state:
    _render_results(st.session_state["rag_eval_results"])

st.markdown("---")
st.caption("SSOT: tests/integration/test_rag_quality_evaluation.py | 검증 기준: 검색율 >= 90%, 응답율 >= 90%")
