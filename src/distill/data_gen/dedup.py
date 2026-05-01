"""MinHash LSH 기반 전역 fingerprint dedup.

기존 dataset_builder 의 dedup 은 ``seen_questions.values()[-200:]`` 슬라이싱으로
**마지막 200 개만** 비교 — 같은 답변의 paraphrase 가 200 개 떨어진 곳에 있으면
못 잡고 train/eval 양쪽에 들어가서 학습 데이터 누수의 주된 경로.

GlobalDeduper 는 datasketch.MinHashLSH 로 전 데이터에 대해 O(n) 검사.
Q+A 합쳐서 fingerprint 화 — 같은 답에 다른 표현 도 한 쪽에 들어가도록.
"""

from __future__ import annotations

import hashlib

from datasketch import MinHash, MinHashLSH


def chunk_fingerprint(content: str) -> str:
    """청크 content 의 deterministic fingerprint (chunk-level partition 용).

    train QA 가 만들어진 chunk 와 test QA 가 만들어질 chunk 가 같은 텍스트 인지
    빠르게 비교하기 위함. 짧은 16자 hash — 충돌 가능성 매우 낮음.
    """
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]


def _shingles(text: str, k: int = 3) -> list[str]:
    """k-shingle 추출 (한국어 대응 — 공백 제거 후 char k-gram)."""
    text = text.replace(" ", "").lower()
    if len(text) < k:
        return [text] if text else []
    return [text[i : i + k] for i in range(len(text) - k + 1)]


def _minhash(qa: dict, num_perm: int = 128) -> MinHash:
    fingerprint = (qa.get("question", "") + " " + qa.get("answer", "")).strip()
    m = MinHash(num_perm=num_perm)
    for sh in _shingles(fingerprint):
        m.update(sh.encode("utf-8"))
    return m


class GlobalDeduper:
    """전역 LSH dedup — Q+A 합쳐서 MinHash fingerprint."""

    def __init__(self, threshold: float = 0.85, num_perm: int = 128) -> None:
        self.lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self.num_perm = num_perm
        self._counter = 0

    def is_duplicate(self, qa: dict) -> bool:
        m = _minhash(qa, self.num_perm)
        return bool(self.lsh.query(m))

    def add(self, qa: dict) -> bool:
        """중복 아니면 add 후 True, 중복이면 drop 하고 False."""
        m = _minhash(qa, self.num_perm)
        if self.lsh.query(m):
            return False
        key = f"qa-{self._counter}"
        self.lsh.insert(key, m)
        self._counter += 1
        return True


def deduplicate_qa(
    rows: list[dict], *, threshold: float = 0.85,
) -> list[dict]:
    """rows 를 GlobalDeduper 로 dedup — 입력 순서 보존."""
    d = GlobalDeduper(threshold=threshold)
    out: list[dict] = []
    for row in rows:
        if d.add({"question": row.get("question", ""), "answer": row.get("answer", "")}):
            out.append(row)
    return out
