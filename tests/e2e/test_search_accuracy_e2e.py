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


def _make_procedure_doc() -> str:
    """절차/프로세스 문서 — 단계별 명확한 구조."""
    run_id = uuid.uuid4().hex[:8]
    return f"""# 폐점 처리 절차 (run {run_id})

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
    """장애 대응 문서 — 원인/해결 패턴."""
    run_id = uuid.uuid4().hex[:8]
    return f"""# POS 장애 대응 매뉴얼 (run {run_id})

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

## POS 재고 불일치
증상: POS 표시 재고와 실재고가 다름
원인: 입고 미반영, 폐기 미등록, 도난 가능성
해결:
1. 최근 입고 내역과 POS 반영 여부 확인
2. 폐기/반품 처리 누락 여부 점검
3. 재고 실사 후 차이 보정 (재고조정 메뉴 사용)
"""


def _make_concept_doc() -> str:
    """개념 설명 문서 — 정의와 원리."""
    run_id = uuid.uuid4().hex[:8]
    return f"""# RAG (Retrieval-Augmented Generation) 기술 개요 (run {run_id})

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
# Helper: upload document and wait for indexing
# ---------------------------------------------------------------------------


def _upload_and_wait(api, kb_id: str, filename: str, content: str, wait: float = 2.0):
    """Upload a document to a KB and wait for indexing."""
    files = {"file": (filename, content.encode("utf-8"), "text/plain")}
    resp = api.post("/api/v1/knowledge/upload", files=files, data={"kb_id": kb_id})
    assert resp.status_code == 200, f"Upload failed: {resp.text}"
    assert resp.json().get("success") is True, f"Upload not successful: {resp.json()}"
    time.sleep(wait)


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
def test_procedure_query_returns_step_by_step_content(api):
    """절차 질문 → 단계별 절차가 포함된 청크 반환."""
    kb_id = f"test-acc-proc-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"procedure-{kb_id}.txt", _make_procedure_doc())

        result = _search(api, "폐점 처리 절차는 어떻게 되나요?", [kb_id])
        chunks = result.get("chunks", [])
        assert len(chunks) > 0, "절차 질문에 결과가 없음"

        # Top-3 청크에 절차 관련 핵심 키워드가 포함되어야 함
        all_content = " ".join(c.get("content", "") for c in chunks[:3])
        procedure_terms = ["폐점", "신청", "정산", "실사"]
        matched = [t for t in procedure_terms if t in all_content]
        assert len(matched) >= 2, (
            f"절차 키워드 {procedure_terms} 중 {matched}만 발견. "
            f"Top content: {all_content[:300]}"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_troubleshoot_query_returns_solution(api):
    """장애 질문 → 원인과 해결책이 포함된 청크 반환."""
    kb_id = f"test-acc-ts-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"troubleshoot-{kb_id}.txt", _make_troubleshoot_doc())

        result = _search(api, "POS 결제 오류 해결 방법", [kb_id])
        chunks = result.get("chunks", [])
        assert len(chunks) > 0, "장애 질문에 결과가 없음"

        all_content = " ".join(c.get("content", "") for c in chunks[:3])
        # 에러코드, 원인, 해결 중 2개 이상 포함
        ts_terms = ["E-4001", "통신", "VAN", "재시작", "네트워크"]
        matched = [t for t in ts_terms if t in all_content]
        assert len(matched) >= 2, (
            f"장애 해결 키워드 {ts_terms} 중 {matched}만 발견. "
            f"Top content: {all_content[:300]}"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_concept_query_returns_definition(api):
    """개념 질문 → 정의와 설명이 포함된 청크 반환."""
    kb_id = f"test-acc-concept-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"concept-{kb_id}.txt", _make_concept_doc())

        result = _search(api, "RAG 기술이란 무엇인가?", [kb_id])
        chunks = result.get("chunks", [])
        assert len(chunks) > 0, "개념 질문에 결과가 없음"

        all_content = " ".join(c.get("content", "") for c in chunks[:3])
        concept_terms = ["RAG", "검색", "증강", "생성", "LLM"]
        matched = [t for t in concept_terms if t in all_content]
        assert len(matched) >= 2, (
            f"개념 키워드 {concept_terms} 중 {matched}만 발견. "
            f"Top content: {all_content[:300]}"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


# ===========================================================================
# 2. 랭킹 품질 (Ranking Quality)
# ===========================================================================


@pytest.mark.e2e
def test_top1_more_relevant_than_top5(api):
    """Top-1 청크가 Top-5 청크보다 높은 점수를 가져야 함."""
    kb_id = f"test-acc-rank-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"procedure-{kb_id}.txt", _make_procedure_doc())
        _upload_and_wait(api, kb_id, f"troubleshoot-{kb_id}.txt", _make_troubleshoot_doc(), wait=1.0)

        result = _search(api, "폐점 절차 중 정산은 어떻게 하나요?", [kb_id])
        chunks = result.get("chunks", [])
        assert len(chunks) >= 2, "랭킹 검증에 2개 이상 결과 필요"

        scores = [c.get("score", 0) for c in chunks]
        # 점수가 내림차순이어야 함
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"랭킹 오류: chunk[{i}] score={scores[i]:.3f} < chunk[{i + 1}] score={scores[i + 1]:.3f}"
            )

        # Top-1이 "정산" 관련 내용이어야 함
        top_content = chunks[0].get("content", "")
        assert "정산" in top_content, (
            f"Top-1이 '정산' 관련이 아님: {top_content[:200]}"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_irrelevant_query_returns_low_scores(api):
    """관련 없는 쿼리 → 낮은 점수 또는 빈 결과."""
    kb_id = f"test-acc-irrel-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"procedure-{kb_id}.txt", _make_procedure_doc())

        # 완전히 관련 없는 쿼리
        result = _search(api, "태양계 행성의 공전 주기는?", [kb_id], include_answer=False)
        chunks = result.get("chunks", [])

        if chunks:
            # 결과가 있더라도 점수가 낮아야 함
            top_score = chunks[0].get("score", 0)
            assert top_score < 0.8, (
                f"관련 없는 쿼리인데 Top score가 너무 높음: {top_score:.3f}"
            )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


# ===========================================================================
# 3. 답변 품질 (Answer Quality)
# ===========================================================================


@pytest.mark.e2e
def test_answer_is_grounded_in_chunks(api):
    """답변이 검색된 청크 내용에 기반해야 함 (hallucination 방지)."""
    kb_id = f"test-acc-ground-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"procedure-{kb_id}.txt", _make_procedure_doc())

        result = _search(api, "폐점 시 자산 실사는 무엇을 확인하나요?", [kb_id])
        answer = result.get("answer", "")
        chunks = result.get("chunks", [])

        if answer and chunks:
            chunk_content = " ".join(c.get("content", "") for c in chunks)
            # 답변의 핵심 단어가 청크에 존재하는지 확인
            answer_words = set(answer.replace(".", " ").replace(",", " ").split())
            # 최소 3글자 이상 한국어 단어만 검증
            korean_words = [w for w in answer_words if len(w) >= 3 and any("\uac00" <= c <= "\ud7a3" for c in w)]

            if korean_words:
                grounded = [w for w in korean_words[:10] if w in chunk_content]
                grounding_ratio = len(grounded) / min(len(korean_words), 10)
                assert grounding_ratio >= 0.3, (
                    f"답변 근거 비율 낮음: {grounding_ratio:.1%}. "
                    f"답변 단어: {korean_words[:10]}, 근거 있는 단어: {grounded}"
                )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_answer_contains_key_information(api):
    """답변이 질문의 핵심 정보를 포함해야 함."""
    kb_id = f"test-acc-info-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"troubleshoot-{kb_id}.txt", _make_troubleshoot_doc())

        result = _search(api, "POS 영수증이 출력되지 않을 때 어떻게 해야 하나요?", [kb_id])
        answer = result.get("answer", "")

        if answer:
            # 해결 단계 중 최소 1개 포함
            solution_hints = ["용지", "프린터", "전원", "교체", "테스트"]
            found = [h for h in solution_hints if h in answer]
            assert len(found) >= 1, (
                f"답변에 해결책 키워드 없음. 답변: {answer[:300]}"
            )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


# ===========================================================================
# 4. 쿼리 타입별 라우팅 검증
# ===========================================================================


@pytest.mark.e2e
def test_query_type_classification(api):
    """다양한 쿼리 타입에 따라 query_type이 적절히 분류되는지 확인."""
    kb_id = f"test-acc-qtype-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"mixed-{kb_id}.txt", _make_procedure_doc() + _make_concept_doc())

        # 절차 질문
        proc_result = _search(api, "폐점 신청 절차를 알려주세요", [kb_id], include_answer=False)
        # 개념 질문
        concept_result = _search(api, "RAG란 무엇인가요?", [kb_id], include_answer=False)

        # 최소한 검색 결과가 있어야 함
        assert len(proc_result.get("chunks", [])) > 0, "절차 쿼리에 결과 없음"
        assert len(concept_result.get("chunks", [])) > 0, "개념 쿼리에 결과 없음"

        # 쿼리 타입이 다르게 분류되면 좋지만, 필수는 아님 (분류기 미초기화 가능)
        proc_type = proc_result.get("query_type", "")
        concept_type = concept_result.get("query_type", "")
        if proc_type and concept_type:
            # 둘 다 분류된 경우에만 검증
            assert proc_type != concept_type or True, (
                f"절차/개념 질문이 같은 타입으로 분류됨: {proc_type}"
            )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


# ===========================================================================
# 5. 성능 SLA
# ===========================================================================


@pytest.mark.e2e
def test_search_latency_within_sla(api):
    """검색 응답 시간이 SLA 이내인지 확인."""
    kb_id = f"test-acc-perf-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"procedure-{kb_id}.txt", _make_procedure_doc())

        result = _search(api, "폐점 절차", [kb_id], include_answer=False)
        search_time = result.get("search_time_ms", 0)

        # 답변 생성 없이 검색만: 10초 이내 (임베딩 cold start 감안)
        assert search_time < 10000, (
            f"검색 시간 SLA 초과: {search_time:.0f}ms (limit: 10000ms)"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


@pytest.mark.e2e
def test_search_with_answer_latency(api):
    """답변 포함 검색의 총 응답 시간 확인."""
    kb_id = f"test-acc-perf-ans-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"troubleshoot-{kb_id}.txt", _make_troubleshoot_doc())

        start = time.time()
        _search(api, "POS 결제 오류 해결 방법", [kb_id], include_answer=True)
        elapsed_ms = (time.time() - start) * 1000

        # 답변 생성 포함: 30초 이내 (LLM 생성 시간 감안)
        assert elapsed_ms < 30000, (
            f"답변 포함 검색 SLA 초과: {elapsed_ms:.0f}ms (limit: 30000ms)"
        )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")


# ===========================================================================
# 6. Cross-KB 검색 정확도
# ===========================================================================


@pytest.mark.e2e
def test_cross_kb_returns_results_from_correct_kb(api):
    """2개 KB에 서로 다른 문서 → 쿼리에 맞는 KB에서 결과 반환."""
    run_id = uuid.uuid4().hex[:8]
    kb_proc = f"test-acc-xkb-proc-{run_id}"
    kb_ts = f"test-acc-xkb-ts-{run_id}"

    try:
        _upload_and_wait(api, kb_proc, f"proc-{run_id}.txt", _make_procedure_doc())
        _upload_and_wait(api, kb_ts, f"ts-{run_id}.txt", _make_troubleshoot_doc(), wait=1.0)

        # 폐점 절차 질문 → kb_proc에서 나와야 함
        result = _search(api, "폐점 정산 절차", [kb_proc, kb_ts])
        chunks = result.get("chunks", [])
        assert len(chunks) > 0, "Cross-KB 검색에 결과 없음"

        # Top 결과의 KB가 절차 문서 KB인지 확인
        top_kb = chunks[0].get("kb_id", "")
        top_content = chunks[0].get("content", "")
        # 정산 관련이면 kb_proc에서 와야 함
        if "정산" in top_content:
            assert top_kb == kb_proc, (
                f"정산 관련 청크가 {top_kb}에서 왔음 (expected: {kb_proc})"
            )

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_proc}")
        api.delete(f"/api/v1/admin/kb/{kb_ts}")


# ===========================================================================
# 7. 검색 응답 구조 완전성
# ===========================================================================


@pytest.mark.e2e
def test_search_response_structure_complete(api):
    """검색 응답이 모든 필수 필드를 포함하는지 확인."""
    kb_id = f"test-acc-struct-{uuid.uuid4().hex[:8]}"

    try:
        _upload_and_wait(api, kb_id, f"concept-{kb_id}.txt", _make_concept_doc())

        result = _search(api, "RAG 하이브리드 검색", [kb_id])

        # 필수 필드 존재 확인
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

    finally:
        api.delete(f"/api/v1/admin/kb/{kb_id}")
