"""OCR noise detection and LLM correction.

Detects OCR artifacts (broken jamo, meaningless repetitions, garbled syllables)
and corrects them using the local LLM (EXAONE via Ollama).

Also provides domain-dictionary-based correction for OCR misreads that
produce valid Korean syllables (e.g., "얼업활설화" → "영업활성화").

Adapted from oreo-ecosystem ocr_noise_detector.py + fix_ocr_chunks.py.

Usage:
    from src.pipeline.ocr_corrector import needs_correction, correct_ocr_text

    if needs_correction(text):
        corrected = await correct_ocr_text(text, ollama_client)
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# Korean jamo (consonants/vowels alone) — rare in normal text
_JAMO_RE = re.compile(r"[\u3131-\u3163\u11A8-\u11FF]")

# Meaningless character repetition (e.g., "ㅋㅋㅋㅋ" or "====")
_REPEAT_RE = re.compile(r"(.)\1{3,}")

# Garbled OCR syllable combinations
_NOISE_SYLLABLES = re.compile(
    r"[륙곰슨묻릉룬륨름륵몽룸뭬늘믄뉘녈][어은을룸름릉늙류늘는근]"
)


# ---------------------------------------------------------------------------
# Domain dictionary for OCR misread correction
# OCR often produces valid but wrong Korean syllables (e.g., 영→얼, 성→설)
# These corrections are applied deterministically without LLM.
# ---------------------------------------------------------------------------

_DOMAIN_TERMS: list[str] = [
    # GS리테일 도메인 용어
    "영업활성화", "장려금", "공헌이익", "가맹점", "경영주", "폐점", "양수도",
    "매출신장", "일매출", "총매출", "객단가", "접객", "점포개선",
    "재계약", "월정액", "정산금", "위약금", "수수료",
    "상권분석", "경쟁점", "진열개선", "카테고리", "중분류",
    "브랜드전환", "리뉴얼", "시설점검", "협력사", "파트너",
    "손익계산서", "계정과목", "세금계산서", "전자전표",
    # IT 도메인 용어
    "배포목록", "시스템", "프로젝트", "데이터베이스", "서버",
    "인증심사", "취약점", "보안환경", "모의해킹",
    # 추가 GS리테일 용어
    "특별영업", "영업장려", "가맹수수료", "인테리어", "점포코드",
    "경쟁분석", "고객설문", "상품구색", "편의점", "매출분석",
    "배달서비스", "택배수수료", "보증보험", "사업자등록",
    # 추가 IT 용어
    "인터페이스", "프로그램", "스프린트", "대시보드", "모니터링",
    "아키텍처", "클라우드", "컨테이너", "쿠버네티스", "마이크로서비스",
]


# ---------------------------------------------------------------------------
# OCR post-processing: clean common OCR artifacts in chunk text
# These are deterministic, fast, and safe to apply to existing chunks.
# ---------------------------------------------------------------------------

def clean_ocr_spacing(text: str) -> str:
    """Fix broken Korean word spacing from OCR syllable splitting.

    Merges isolated single-syllable Hangul characters back together.
    E.g., "형 열 및 및 나" → "형열 및 및 나"
    """
    if not text:
        return text
    # Merge sequences of single Korean syllables separated by spaces
    # Pattern: 가 나 다 → 가나다 (3+ consecutive single chars)
    result = re.sub(
        r"(?<=[가-힣])\s(?=[가-힣](?:\s[가-힣]){2,})",
        "", text,
    )
    return result


def clean_ocr_numbers(text: str) -> str:
    """Fix corrupted comma-separated numbers from OCR.

    E.g., "159,0008" → "159,000", "95,8409" → "95,840"
    """
    if not text:
        return text
    # Fix numbers where OCR added an extra digit after a comma group
    result = re.sub(r"(\d{1,3}(?:,\d{3})+)\d(?!\d)", r"\1", text)
    return result


_OCR_TAG_RE = re.compile(r"\[(?:Page|Image|Slide)\s+\d+\s+OCR\]")


def dedup_ocr_sections(text: str) -> str:
    """Remove duplicate OCR extraction sections.

    When both [Page N OCR] and [Image N OCR] extract the same content,
    keep only the first occurrence.
    """
    if not text or "[OCR]" not in text:
        return text

    # Split by OCR tags
    sections = re.split(r"(\[(?:Page|Image|Slide)\s+\d+\s+OCR\])", text)
    seen_content: set[str] = set()
    result_parts: list[str] = []

    i = 0
    while i < len(sections):
        part = sections[i]
        if not _OCR_TAG_RE.match(part):
            result_parts.append(part)
            i += 1
            continue

        # This is a tag — check if next section's content is duplicate
        content = sections[i + 1] if i + 1 < len(sections) else ""
        content_key = re.sub(r"\s+", "", content)[:200]  # normalize for comparison
        if content_key and content_key in seen_content:
            i += 2  # skip tag + content (duplicate)
            continue
        if content_key:
            seen_content.add(content_key)
        result_parts.append(part)
        if i + 1 < len(sections):
            result_parts.append(sections[i + 1])
        i += 2

    return "".join(result_parts)


def clean_chunk_text(text: str) -> str:
    """Apply all OCR cleaning passes to chunk text.

    Safe for batch processing — deterministic, no LLM needed.
    """
    if not text:
        return text
    text = dedup_ocr_sections(text)
    text = clean_ocr_spacing(text)
    text = clean_ocr_numbers(text)
    text = _correct_with_domain_dict(text)
    return text


_CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def _get_choseong(text: str) -> str:
    """Extract Korean initial consonants (초성) from text."""
    result = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            result.append(_CHOSEONG[code // 588])
    return "".join(result)


def _score_term_match(token: str, term: str) -> float | None:
    """Score a token-term pair. Returns weighted score or None if no match."""
    if abs(len(token) - len(term)) > 1:
        return None
    ratio = SequenceMatcher(None, token, term).ratio()

    # Tier 1: high char similarity
    if ratio >= 0.75:
        return ratio
    # Tier 2: choseong match + moderate similarity
    if ratio >= 0.5 and len(token) == len(term) and _get_choseong(token) == _get_choseong(term):
        return ratio + 0.3  # boost for choseong match
    return None


def _find_best_domain_match(token: str) -> tuple[str, float]:
    """Find the best matching domain term for a token."""
    best_term = ""
    best_score = 0.0
    for term in _DOMAIN_TERMS:
        score = _score_term_match(token, term)
        if score is not None and score > best_score:
            best_score = score
            best_term = term
    return best_term, best_score


def _correct_with_domain_dict(text: str) -> str:
    """Correct OCR misreads using domain dictionary.

    Two-tier matching:
    1. High similarity (≥0.75): direct character-level match
    2. Choseong match + moderate similarity (≥0.5): catches OCR errors
       that preserve consonant structure (e.g., 얼업활설화 → 영업활성화)

    Args:
        text: Input text potentially containing OCR misreads.

    Returns:
        Text with domain term corrections applied.
    """
    if not text:
        return text

    corrections_made = 0
    result = text

    # Extract Korean tokens (2-8 chars)
    tokens = re.findall(r"[가-힣]{2,8}", text)
    seen: set[str] = set()

    for token in tokens:
        if token in seen or token in _DOMAIN_TERMS:
            continue
        seen.add(token)

        best_term, best_score = _find_best_domain_match(token)
        if best_term:
            result = result.replace(token, best_term)
            corrections_made += 1
            logger.debug("OCR domain fix: '%s' → '%s' (score=%.2f)", token, best_term, best_score)

    if corrections_made:
        logger.info("OCR domain corrections: %d terms fixed", corrections_made)
    return result


def noise_score(text: str) -> float:
    """Compute OCR noise score. Returns 0.0~1.0, higher = more noise."""
    if not text:
        return 0.0
    text_len = max(1, len(text))

    jamo_count = len(_JAMO_RE.findall(text))
    repeat_count = len(_REPEAT_RE.findall(text))
    noise_syl_count = len(_NOISE_SYLLABLES.findall(text))

    score = (jamo_count * 2 + repeat_count * 3 + noise_syl_count * 5) / text_len
    return min(1.0, score * 10)


def needs_correction(text: str, threshold: float = 0.05) -> bool:
    """Determine if text needs LLM-based OCR correction.

    Two-tier threshold:
    - Chunks with [OCR] tag: base threshold
    - Chunks without [OCR] tag: threshold * 2
    """
    if "[OCR]" in text and noise_score(text) >= threshold:
        return True
    return noise_score(text) >= threshold * 2


_CORRECTION_PROMPT = """아래 텍스트는 PPT/PDF 문서에서 OCR로 추출한 것입니다.
OCR 오류로 인해 한글이 깨지거나 의미없는 문자가 섞여 있습니다.

**규칙:**
1. 깨진 한글/의미없는 문자는 제거하거나 문맥상 올바른 단어로 교정
2. 숫자, 날짜, 퍼센트 등 데이터는 최대한 보존
3. 원본 구조(줄바꿈, 항목 번호 등)는 유지
4. 추측으로 내용을 추가하지 말 것 — 원본에 없는 정보 금지
5. [Image N OCR] 같은 메타데이터 태그는 그대로 유지

**원본 텍스트:**
{text}

**교정된 텍스트:**"""


async def correct_ocr_text(text: str, ollama_client) -> str:
    """Correct OCR noise using domain dictionary + LLM (EXAONE via Ollama).

    Two-stage correction:
    1. Domain dictionary: fast, deterministic fix for known term misreads
    2. LLM: handles remaining noise (broken jamo, garbled text)

    Args:
        text: OCR-extracted text with potential noise.
        ollama_client: OllamaClient instance with generate() method.

    Returns:
        Corrected text, or original if correction fails or is too short.
    """
    if not text:
        return text

    # Stage 1: Domain dictionary correction (always applied, no threshold)
    text = _correct_with_domain_dict(text)

    if not needs_correction(text):
        return text

    score_before = noise_score(text)
    logger.info("OCR correction: noise_score=%.3f, text_len=%d", score_before, len(text))

    try:
        input_text = text[:3000]  # limit input length
        prompt = _CORRECTION_PROMPT.format(text=input_text)
        corrected = await ollama_client.generate(prompt, temperature=0.0, max_tokens=3000)
        corrected = corrected.strip()

        # Safety: compare against input length (not full original) to avoid false rejection
        if len(corrected) < len(input_text) * 0.3:
            logger.warning(
                "OCR correction too short (%d vs %d), keeping original",
                len(corrected), len(text),
            )
            return text

        score_after = noise_score(corrected)
        logger.info(
            "OCR correction: noise %.3f -> %.3f, len %d -> %d",
            score_before, score_after, len(text), len(corrected),
        )
        return corrected

    except Exception as e:  # noqa: BLE001
        logger.warning("OCR LLM correction failed: %s", e)
        return text


async def correct_ocr_chunks(
    ocr_text: str,
    ollama_client,
    _chunk_size: int = 2000,
) -> str:
    """Correct OCR text in chunks to handle long texts.

    Splits by [Image N OCR] tags, corrects each chunk, rejoins.
    """
    if not ocr_text or not needs_correction(ocr_text):
        return ocr_text

    # Split by [Image N OCR] tags
    parts = re.split(r"(\[Image \d+ OCR\])", ocr_text)
    corrected_parts: list[str] = []

    current_chunk = ""
    for part in parts:
        if re.match(r"\[Image \d+ OCR\]", part):
            # Correct accumulated chunk
            if current_chunk.strip() and needs_correction(current_chunk):
                current_chunk = await correct_ocr_text(current_chunk, ollama_client)
            corrected_parts.append(current_chunk)
            corrected_parts.append(part)
            current_chunk = ""
        else:
            current_chunk += part

    # Last chunk
    if current_chunk.strip() and needs_correction(current_chunk):
        current_chunk = await correct_ocr_text(current_chunk, ollama_client)
    corrected_parts.append(current_chunk)

    return "".join(corrected_parts)
