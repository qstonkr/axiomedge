"""프롬프트 인젝션 방어 공통 유틸.

LLM prompt 에 user-controlled 데이터 (질문, 답변, 청크 내용 등) 를 주입할 때
아래 두 가지를 강제한다:

1. **XML/텍스트 delimiter 래핑** — user input 을 ``<question>...</question>``
   같은 태그 안에 넣어 시스템 지시문과 구분. 동시에 내부 닫힘 태그는 escape.
2. **Instruction keyword sanitization** — "ignore previous", "무시하고 다음을",
   "SEMANTIC=YES" 같은 지시어/약속된 output token 이 입력에 섞여 있으면 중화
   ([BLOCKED] 로 치환) 후 주입.

또한 LLM 응답 파싱은 substring 매칭 금지:
- 응답 첫 줄만 검사
- 정확한 구분자 + 값 패턴 (예: ``SEMANTIC=YES LEAK=NO``)
- JSON schema 강제 가능 시 ``parse_strict_verdict`` 사용

이 모듈은 ``src/llm/utils.py::sanitize_text`` 와 별개 — 그쪽은 사용자 쿼리
중의 ``[BLOCKED]`` 치환 전용이지만, 이 모듈은 prompt 템플릿 주입 경계 전반에
적용된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Delimiter wrapping
# ---------------------------------------------------------------------------

# 사용자 데이터 안에 이 태그가 그대로 포함되면 delimiter confusion 이 발생.
# escape 후 주입해야 한다.
_DANGEROUS_TAG_PATTERN = re.compile(
    r"</?(question|answer|variation|context|chunk|system|instruction|user)>",
    re.IGNORECASE,
)


def _escape_tags(text: str) -> str:
    """user input 내부의 delimiter 태그를 중화.

    예: ``</answer>`` → ``[/answer]`` — 닫힘 태그 의미를 제거하여
    LLM 이 "여기서 user input 끝났다" 고 오해하지 않게 한다.
    """
    return _DANGEROUS_TAG_PATTERN.sub(
        lambda m: m.group(0).replace("<", "[").replace(">", "]"),
        text,
    )


def wrap(tag: str, text: str, *, max_len: int | None = None) -> str:
    """사용자 입력을 ``<tag>...</tag>`` delimiter 로 감싼다.

    - 내부 delimiter 태그는 escape
    - ``max_len`` 지정 시 character 기반 truncate
    - 앞뒤 공백 정리

    Args:
        tag: 태그명 (e.g. "question", "answer"). 영문 소문자 권장.
        text: user input.
        max_len: 최대 문자 수. None 이면 truncate 없음.

    Returns:
        ``<tag>escaped_text</tag>`` 형태의 문자열.
    """
    if text is None:
        text = ""
    cleaned = _escape_tags(str(text)).strip()
    if max_len is not None and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip() + "…"
    return f"<{tag}>{cleaned}</{tag}>"


# ---------------------------------------------------------------------------
# Instruction keyword sanitization
# ---------------------------------------------------------------------------

# LLM 에 넘어가면 시스템 지시문을 우회할 수 있는 패턴들.
# ``src/llm/utils.py::_INJECTION_PATTERNS`` 와 상호 보완 — 여기는 distill
# 데이터 생성 경로 전용이고, 약속된 output 토큰 (e.g. SEMANTIC=YES LEAK=NO)
# 이 사용자 입력에 섞여 들어가 judge 를 속이는 것도 방어한다.
_INSTRUCTION_PATTERNS = re.compile(
    r"(?i)"
    # English instruction bypass
    r"(?:ignore\s+(?:all\s+)?(?:previous|above|prior))"
    r"|(?:disregard\s+(?:all\s+)?(?:previous|above|prior))"
    r"|(?:forget\s+(?:all\s+)?(?:previous|above|prior))"
    r"|(?:new\s+instructions?)"
    r"|(?:system\s*:|system\s*prompt)"
    # Korean instruction bypass
    r"|(?:이전\s*지시\s*(?:는|을)?\s*무시)"
    r"|(?:위\s*지시\s*(?:는|을)?\s*무시)"
    r"|(?:다음\s*지시\s*(?:를|을)?\s*따르)"
    r"|(?:시스템\s*(?:지시|프롬프트)\s*[:：])"
    r"|(?:새\s*지시)"
    r"|(?:무시하고\s*다음)"
    # Promised output tokens — judge 우회 방어
    r"|(?:\bSEMANTIC\s*=\s*YES\b)"
    r"|(?:\bSEMANTIC\s*=\s*NO\b)"
    r"|(?:\bLEAK\s*=\s*YES\b)"
    r"|(?:\bLEAK\s*=\s*NO\b)",
)

_BLOCKED = "[BLOCKED]"


def neutralize_instructions(text: str) -> str:
    """지시어/약속된 output 토큰을 ``[BLOCKED]`` 로 중화.

    사용자 입력 안에서만 적용하고 system prompt 는 변경하지 않는다.
    """
    if not text:
        return text
    return _INSTRUCTION_PATTERNS.sub(_BLOCKED, text)


def safe_user_input(tag: str, text: str, *, max_len: int | None = None) -> str:
    """``neutralize_instructions`` + ``wrap`` 조합.

    거의 모든 prompt 주입 지점에서 이것만 쓰면 된다.

    Args:
        tag: delimiter 태그 이름.
        text: user input (question, answer, chunk 등).
        max_len: 문자 길이 제한.

    Returns:
        ``<tag>neutralized_and_escaped</tag>``
    """
    neutralized = neutralize_instructions(str(text or ""))
    return wrap(tag, neutralized, max_len=max_len)


# ---------------------------------------------------------------------------
# Strict output parsing — LLM judge 응답용
# ---------------------------------------------------------------------------

@dataclass
class VerdictParseResult:
    """``SEMANTIC=YES LEAK=NO`` 형태 응답의 엄격한 파싱 결과."""

    ok: bool  # 파싱 + 두 조건 모두 만족 시 True
    semantic: bool | None  # YES=True, NO=False, 파싱 실패 시 None
    leak: bool | None
    reason: str  # 실패 이유 (debugging/logging)


# 응답 첫 줄에서 두 키/값 쌍을 엄격하게 추출. substring 매칭 금지.
_VERDICT_PATTERN = re.compile(
    r"^\s*SEMANTIC\s*=\s*(YES|NO)\s+LEAK\s*=\s*(YES|NO)\s*$",
    re.IGNORECASE,
)


def parse_strict_verdict(response: str) -> VerdictParseResult:
    """LLM judge 응답을 엄격 파싱.

    - 응답 **첫 비어있지 않은 줄** 만 검사 (이후 줄 무시 — hidden injection 차단)
    - 정확한 정규식 매칭. substring 매칭 금지.
    - 대소문자 무시 (SEMANTIC/semantic 둘 다 허용)

    ``ok=True`` 는 ``SEMANTIC=YES`` AND ``LEAK=NO`` 일 때만.
    """
    if not response:
        return VerdictParseResult(False, None, None, "empty_response")

    # 첫 비어있지 않은 줄
    first_line = ""
    for line in response.splitlines():
        if line.strip():
            first_line = line
            break
    if not first_line:
        return VerdictParseResult(False, None, None, "no_non_empty_line")

    m = _VERDICT_PATTERN.match(first_line)
    if not m:
        return VerdictParseResult(
            False, None, None, f"pattern_mismatch({first_line[:80]!r})",
        )

    semantic = m.group(1).upper() == "YES"
    leak = m.group(2).upper() == "YES"
    ok = semantic and not leak
    return VerdictParseResult(
        ok=ok,
        semantic=semantic,
        leak=leak,
        reason="ok" if ok else f"semantic={semantic} leak={leak}",
    )


# ---------------------------------------------------------------------------
# Strict float parsing — generality / quality judge 점수용
# ---------------------------------------------------------------------------

_FLOAT_FIRST_LINE_PATTERN = re.compile(r"^\s*([01](?:\.\d+)?|0?\.\d+)\s*$")


def parse_strict_score(response: str) -> float | None:
    """응답 첫 줄이 ``0.0~1.0`` 범위의 단일 float 인지 엄격 검사.

    - 첫 비어있지 않은 줄만 검사
    - "점수: 0.85" 같은 prefix/suffix 거부 → LLM 이 규정 형식 어기면 None
    - 공격자가 답변에 "1" 같은 문자열을 심어도 응답 첫 줄 아니면 무시됨

    호출자는 None 을 받으면 **rejection 처리** 하거나 보수적 fallback 적용.
    """
    if not response:
        return None
    for line in response.splitlines():
        if not line.strip():
            continue
        m = _FLOAT_FIRST_LINE_PATTERN.match(line)
        if not m:
            return None
        try:
            val = float(m.group(1))
        except ValueError:
            return None
        if 0.0 <= val <= 1.0:
            return val
        return None
    return None
