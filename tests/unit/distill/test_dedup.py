"""GlobalDeduper — MinHash LSH 기반 전역 fingerprint dedup."""

from src.distill.data_gen.dedup import GlobalDeduper


def test_add_returns_true_for_first():
    d = GlobalDeduper(threshold=0.85)
    assert d.add({"question": "영업시간은?", "answer": "9시-22시"}) is True


def test_add_returns_false_for_exact_duplicate():
    d = GlobalDeduper(threshold=0.85)
    qa = {"question": "영업시간은?", "answer": "9시-22시"}
    d.add(qa)
    assert d.add(qa) is False


def test_dedup_paraphrase_close_threshold():
    d = GlobalDeduper(threshold=0.5)  # loose 한 임계
    d.add({"question": "GS25 영업시간 알려주세요", "answer": "9시부터 22시까지"})
    is_dup = d.is_duplicate({
        "question": "GS25 영업 시간 알려 주세요", "answer": "오전9시부터 오후10시까지",
    })
    assert is_dup


def test_dedup_does_not_match_distinct_qa():
    d = GlobalDeduper(threshold=0.85)
    d.add({"question": "영업시간?", "answer": "9-22"})
    assert d.is_duplicate({"question": "직원 휴게실 위치?", "answer": "2층 동쪽"}) is False


def test_global_window_far_apart_paraphrase_caught():
    """200-window 회귀 — 멀리 떨어진 paraphrase 도 잡혀야."""
    d = GlobalDeduper(threshold=0.5)
    d.add({"question": "원본 질문 0번", "answer": "원본 답 0번"})
    for i in range(1, 501):
        d.add({"question": f"전혀 다른 질문 {i}", "answer": f"전혀 다른 답 {i}"})
    assert d.is_duplicate({"question": "원본 질문 0 번", "answer": "원본 답 0 번"})


def test_dedup_handles_korean_shingles():
    d = GlobalDeduper(threshold=0.7)
    d.add({"question": "한국어 질문입니다", "answer": "한국어 답변입니다"})
    assert d.is_duplicate({"question": "한국어 질문입니다", "answer": "한국어 답변입니다"})


def test_dedup_empty_qa_does_not_crash():
    d = GlobalDeduper(threshold=0.85)
    d.add({"question": "", "answer": ""})
    d.is_duplicate({"question": "", "answer": ""})


def test_chunk_fingerprint_deterministic():
    from src.distill.data_gen.dedup import chunk_fingerprint
    a = chunk_fingerprint("같은 청크 내용입니다")
    b = chunk_fingerprint("같은 청크 내용입니다")
    assert a == b
    assert len(a) == 16


def test_chunk_fingerprint_differs_for_different_content():
    from src.distill.data_gen.dedup import chunk_fingerprint
    a = chunk_fingerprint("청크 A")
    b = chunk_fingerprint("청크 B")
    assert a != b


def test_chunk_fingerprint_strips_whitespace():
    from src.distill.data_gen.dedup import chunk_fingerprint
    a = chunk_fingerprint("동일 내용")
    b = chunk_fingerprint("  동일 내용  \n")
    assert a == b


def test_deduplicate_qa_helper():
    """rows -> dedup된 list — dataset_builder 가 사용할 helper."""
    from src.distill.data_gen.dedup import deduplicate_qa

    rows = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q1", "answer": "A1"},  # 정확 중복
        {"question": "Q2", "answer": "A2"},
        {"question": "Q3", "answer": "A3"},
    ]
    out = deduplicate_qa(rows, threshold=0.85)
    assert len(out) == 3
