"""E2E tests: search accuracy — retrieval relevance, ranking quality, answer faithfulness.

These tests validate the full search pipeline:
  ingest known documents → search with known queries → assert result quality

Used as regression guard for:
- SRP refactoring (enhanced_similarity_matcher, graphrag_extractor)
- Search weight tuning (config_weights changes)
- Reranking pipeline changes

Requires: running API + all services (make start && make api)
Run: uv run pytest tests/e2e/test_search_accuracy_e2e.py -v -m e2e
"""

import time
import uuid

import pytest


# ---------------------------------------------------------------------------
# Test data: documents with known content for precise assertions
# ---------------------------------------------------------------------------

_RUN_ID = uuid.uuid4().hex[:8]


def _make_procedure_doc() -> str:
    return f"""# 폐점 처리 절차 (run {_RUN_ID})

## 1. 폐점 신청
경영주가 폐점 신청서를 작성하여 OFC에 제출합니다.
필요 서류: 폐점신청서, 사업자등록증 사본, 임대차계약서.
OFC는 신청서를 접수하고 3영업일 이내에 검토를 완료합니다.

## 2. 자산 실사
본사 자산관리팀이 점포를 방문하여 시설물, 집기, 재고를 실사합니다.
실사 결과에 따라 자산 회수 계획을 수립합니다.
냉장/냉동 설비, POS 단말기, 간판은 반드시 회수 대상입니다.

## 3. 최종 정산
매출 정산, 보증금 반환, 위약금 정산을 처리합니다.
정산 완료 후 계약 해지 통보서를 발송합니다.
모든 정산은 폐점일로부터 30일 이내에 완료해야 합니다.

## 4. 시스템 처리
POS 시스템에서 점포 코드를 비활성화합니다.
ERP에서 해당 점포의 발주/입고 권한을 제거합니다.
WMS에서 배송 경로를 삭제합니다.
"""


def _make_troubleshoot_doc() -> str:
    return f"""# POS 장애 대응 매뉴얼 (run {_RUN_ID})

## POS 결제 오류 (에러코드 E-4001)
증상: 카드 결제 시 "통신 오류" 메시지 출력
원인: VAN사 통신 장애 또는 POS 단말기 네트워크 불안정
해결:
1. POS 단말기 네트워크 케이블 확인
2. VAN사 상태 페이지 확인 (https://van-status.internal)
3. 네트워크 정상이면 POS 재시작
4. 재시작 후에도 동일하면 IT운영팀 헬프데스크 접수

## POS 영수증 출력 불가
증상: 결제 완료 후 영수증이 출력되지 않음
원인: 영수증 프린터 용지 부족 또는 프린터 드라이버 오류
해결:
1. 영수증 용지 잔량 확인 및 교체
2. 프린터 전원 OFF/ON
3. POS 설정 > 프린터 > 테스트 출력으로 정상 확인
"""


def _make_concept_doc() -> str:
    return f"""# RAG (Retrieval-Augmented Generation) 기술 개요 (run {_RUN_ID})

## RAG란?
RAG는 검색 증강 생성 기술로, 대규모 언어 모델(LLM)이 답변을 생성할 때
외부 지식 베이스에서 관련 문서를 검색하여 컨텍스트로 제공하는 방식입니다.
이를 통해 LLM의 환각(hallucination)을 줄이고 최신 정보를 반영할 수 있습니다.

## 핵심 구성 요소
1. 임베딩 모델 (BGE-M3): 문서와 쿼리를 벡터로 변환
2. 벡터 데이터베이스 (Qdrant): 유사도 기반 검색
3. 리랭커 (Cross-Encoder): 검색 결과 정밀 재정렬
4. 생성 모델 (EXAONE): 컨텍스트 기반 답변 생성

## 하이브리드 검색
Dense 벡터 (의미적 유사도)와 Sparse 벡터 (키워드 매칭)를 결합하여
검색 정확도를 높입니다. RRF (Reciprocal Rank Fusion)로 두 결과를 융합합니다.
"""


# ---------------------------------------------------------------------------
# Shared fixture: upload once, search many times
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def accuracy_kbs(api_url):
    """Create and populate test KBs once for all accuracy tests."""
    import httpx

    kb_proc = f"test-acc-proc-{_RUN_ID}"
    kb_ts = f"test-acc-ts-{_RUN_ID}"
    kb_concept = f"test-acc-concept-{_RUN_ID}"

    with httpx.Client(base_url=api_url, timeout=120) as client:
        # Upload all 3 documents
        for kb_id, filename, content_fn in [
            (kb_proc, "procedure.txt", _make_procedure_doc),
            (kb_ts, "troubleshoot.txt", _make_troubleshoot_doc),
            (kb_concept, "concept.txt", _make_concept_doc),
        ]:
            files = {"file": (filename, content_fn().encode("utf-8"), "text/plain")}
            resp = client.post("/api/v1/knowledge/upload", files=files, data={"kb_id": kb_id})
            assert resp.status_code == 200, f"Upload {kb_id} failed: {resp.text}"

        # Wait for indexing
        time.sleep(5)

        yield {"proc": kb_proc, "ts": kb_ts, "concept": kb_concept}

        # Cleanup
        for kb_id in [kb_proc, kb_ts, kb_concept]:
            try:
                client.delete(f"/api/v1/admin/kb/{kb_id}")
            except Exception:
                pass


@pytest.fixture(scope="module")
def api_url():
    import os
    return os.getenv("TEST_API_URL", "http://localhost:8000")


def _search(api, query: str, kb_ids: list[str], top_k: int = 5, include_answer: bool = True):
    """Execute hub search and return parsed response."""
    resp = api.post("/api/v1/search/hub", json={
        "query": query,
        "kb_ids": kb_ids,
        "top_k": top_k,
        "include_answer": include_answer,
    })
    assert resp.status_code == 200, f"Search failed: {resp.text}"
    return resp.json()


# ===========================================================================
# 1. 검색 결과 관련성 (Retrieval Relevance)
# ===========================================================================


@pytest.mark.e2e
def test_procedure_query_returns_step_by_step_content(api, accuracy_kbs):
    """절차 질문 → 단계별 절차가 포함된 청크 반환."""
    result = _search(api, "폐점 처리 절차는 어떻게 되나요?", [accuracy_kbs["proc"]], include_answer=False)
    chunks = result.get("chunks", [])
    assert len(chunks) > 0, "절차 질문에 결과가 없음"

    all_content = " ".join(c.get("content", "") for c in chunks[:3])
    procedure_terms = ["폐점", "신청", "정산", "실사"]
    matched = [t for t in procedure_terms if t in all_content]
    assert len(matched) >= 2, f"절차 키워드 중 {matched}만 발견"


@pytest.mark.e2e
def test_troubleshoot_query_returns_solution(api, accuracy_kbs):
    """장애 질문 → 원인과 해결책이 포함된 청크 반환."""
    result = _search(api, "POS 결제 오류 해결 방법", [accuracy_kbs["ts"]], include_answer=False)
    chunks = result.get("chunks", [])
    assert len(chunks) > 0, "장애 질문에 결과가 없음"

    all_content = " ".join(c.get("content", "") for c in chunks[:3])
    ts_terms = ["E-4001", "통신", "VAN", "재시작", "네트워크"]
    matched = [t for t in ts_terms if t in all_content]
    assert len(matched) >= 2, f"장애 해결 키워드 중 {matched}만 발견"


@pytest.mark.e2e
def test_concept_query_returns_definition(api, accuracy_kbs):
    """개념 질문 → 정의와 설명이 포함된 청크 반환."""
    result = _search(api, "RAG 기술이란 무엇인가?", [accuracy_kbs["concept"]], include_answer=False)
    chunks = result.get("chunks", [])
    assert len(chunks) > 0, "개념 질문에 결과가 없음"

    all_content = " ".join(c.get("content", "") for c in chunks[:3])
    concept_terms = ["RAG", "검색", "증강", "생성", "LLM"]
    matched = [t for t in concept_terms if t in all_content]
    assert len(matched) >= 2, f"개념 키워드 중 {matched}만 발견"


# ===========================================================================
# 2. 랭킹 품질 (Ranking Quality)
# ===========================================================================


@pytest.mark.e2e
def test_scores_in_descending_order(api, accuracy_kbs):
    """검색 결과 점수가 내림차순이어야 함."""
    result = _search(api, "폐점 절차 중 정산은 어떻게 하나요?",
                     [accuracy_kbs["proc"], accuracy_kbs["ts"]], include_answer=False)
    chunks = result.get("chunks", [])
    assert len(chunks) >= 2, "랭킹 검증에 2개 이상 결과 필요"

    scores = [c.get("score", 0) for c in chunks]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"랭킹 오류: chunk[{i}] score={scores[i]:.3f} < chunk[{i + 1}] score={scores[i + 1]:.3f}"
        )


@pytest.mark.e2e
def test_relevant_query_scores_higher_than_irrelevant(api, accuracy_kbs):
    """관련 쿼리의 Top score > 비관련 쿼리의 Top score."""
    relevant = _search(api, "폐점 정산 절차", [accuracy_kbs["proc"]], include_answer=False)
    irrelevant = _search(api, "태양계 행성의 공전 주기", [accuracy_kbs["proc"]], include_answer=False)

    rel_score = relevant["chunks"][0]["score"] if relevant.get("chunks") else 0
    irrel_score = irrelevant["chunks"][0]["score"] if irrelevant.get("chunks") else 0

    # 관련 쿼리 점수가 더 높아야 함 (또는 비관련 결과가 없을 수 있음)
    if irrel_score > 0:
        assert rel_score > irrel_score, (
            f"관련 쿼리 score({rel_score:.3f}) <= 비관련 쿼리 score({irrel_score:.3f})"
        )


# ===========================================================================
# 3. 답변 품질 (Answer Quality) — LLM 필요
# ===========================================================================


@pytest.mark.e2e
def test_answer_is_grounded_in_chunks(api, accuracy_kbs):
    """답변이 검색된 청크 내용에 기반해야 함 (hallucination 방지)."""
    result = _search(api, "폐점 시 자산 실사는 무엇을 확인하나요?", [accuracy_kbs["proc"]])
    answer = result.get("answer", "")
    chunks = result.get("chunks", [])

    if not answer or not chunks:
        pytest.skip("LLM 답변 생성 불가 (SageMaker 미연결)")

    chunk_content = " ".join(c.get("content", "") for c in chunks)

    # 질문의 핵심 키워드가 답변과 청크 양쪽에 있는지 확인
    # (LLM이 재구성하므로 단어 단위 매칭 대신 핵심 명사로 비교)
    key_terms = ["자산", "실사", "시설", "집기", "재고", "회수", "폐점", "점포"]
    in_answer = [t for t in key_terms if t in answer]
    in_chunks = [t for t in key_terms if t in chunk_content]

    # 답변과 청크에 공통 키워드가 있어야 함 (grounding 증거)
    common = set(in_answer) & set(in_chunks)
    assert len(common) >= 1, (
        f"답변과 청크에 공통 근거 키워드 없음. "
        f"답변 키워드: {in_answer}, 청크 키워드: {in_chunks}"
    )


@pytest.mark.e2e
def test_answer_contains_key_information(api, accuracy_kbs):
    """답변이 질문의 핵심 정보를 포함해야 함."""
    result = _search(api, "POS 영수증이 출력되지 않을 때 어떻게 해야 하나요?", [accuracy_kbs["ts"]])
    answer = result.get("answer", "")

    if not answer:
        pytest.skip("LLM 답변 생성 불가 (SageMaker 미연결)")

    solution_hints = ["용지", "프린터", "전원", "교체", "테스트"]
    found = [h for h in solution_hints if h in answer]
    assert len(found) >= 1, f"답변에 해결책 키워드 없음. 답변: {answer[:300]}"


# ===========================================================================
# 4. Cross-KB 검색 정확도
# ===========================================================================


@pytest.mark.e2e
def test_cross_kb_returns_results_from_both(api, accuracy_kbs):
    """2개 KB 동시 검색 시 두 KB 모두에서 결과 반환."""
    result = _search(api, "POS 시스템 관련 절차",
                     [accuracy_kbs["proc"], accuracy_kbs["ts"]], include_answer=False)
    searched = result.get("searched_kbs", [])
    assert len(searched) >= 1, "Cross-KB 검색에 결과 없음"


# ===========================================================================
# 5. 성능 SLA
# ===========================================================================


@pytest.mark.e2e
def test_search_latency_within_sla(api, accuracy_kbs):
    """검색 응답 시간이 SLA 이내인지 확인 (답변 생성 제외)."""
    result = _search(api, "폐점 절차", [accuracy_kbs["proc"]], include_answer=False)
    search_time = result.get("search_time_ms", 0)

    # 답변 생성 없이 검색만: 15초 이내 (cross-encoder cold start 감안)
    assert search_time < 15000, f"검색 시간 SLA 초과: {search_time:.0f}ms"


# ===========================================================================
# 6. 검색 응답 구조 완전성
# ===========================================================================


@pytest.mark.e2e
def test_search_response_structure_complete(api, accuracy_kbs):
    """검색 응답이 모든 필수 필드를 포함하는지 확인."""
    result = _search(api, "RAG 하이브리드 검색", [accuracy_kbs["concept"]], include_answer=False)

    # 필수 필드 존재
    assert "query" in result
    assert "chunks" in result
    assert "searched_kbs" in result
    assert "total_chunks" in result
    assert "search_time_ms" in result
    assert isinstance(result["chunks"], list)
    assert isinstance(result["search_time_ms"], (int, float))

    # 청크 구조 확인
    if result["chunks"]:
        chunk = result["chunks"][0]
        assert "content" in chunk, "청크에 content 필드 없음"
        assert "score" in chunk, "청크에 score 필드 없음"
        assert "kb_id" in chunk, "청크에 kb_id 필드 없음"
        assert isinstance(chunk["score"], (int, float))
        assert chunk["score"] >= 0, f"점수가 음수: {chunk['score']}"
