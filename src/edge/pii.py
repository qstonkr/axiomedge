"""PII 마스킹 — 매장 쿼리 로그 기록 전 적용.

전화번호/카드/이메일/주민번호 패턴을 placeholder 로 치환해 평문 저장 방지.
정규식 기반 — 완벽하진 않지만 흔한 형태 차단.
"""

from __future__ import annotations

import re

# 010/011/016/017/018/019 + (선택) 구분자(-/공백) + 3-4자리 + 구분자 + 4자리
_PHONE = re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b")

# 4-4-4-4 (구분자 선택)
_CARD = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")

# 일반 이메일
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# 주민등록번호 (6-7, 구분자 선택)
_SSN = re.compile(r"\b\d{6}[- ]?\d{7}\b")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # SSN/CARD 를 PHONE 보다 먼저 — 길이 더 길어서 substring 충돌 방지.
    (_SSN, "[SSN]"),
    (_CARD, "[CARD]"),
    (_PHONE, "[PHONE]"),
    (_EMAIL, "[EMAIL]"),
]


def mask_pii(text: str) -> str:
    """text 안의 PII 패턴을 [PHONE]/[CARD]/[EMAIL]/[SSN] 으로 치환."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
