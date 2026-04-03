"""OCR noise detection and LLM correction.

Detects OCR artifacts (broken jamo, meaningless repetitions, garbled syllables)
and corrects them using the local LLM (EXAONE via Ollama).

Adapted from oreo-ecosystem ocr_noise_detector.py + fix_ocr_chunks.py.

Usage:
    from src.pipeline.ocr_corrector import needs_correction, correct_ocr_text

    if needs_correction(text):
        corrected = await correct_ocr_text(text, ollama_client)
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Korean jamo (consonants/vowels alone) — rare in normal text
_JAMO_RE = re.compile(r"[\u3131-\u3163\u11A8-\u11FF]")

# Meaningless character repetition (e.g., "ㅋㅋㅋㅋ" or "====")
_REPEAT_RE = re.compile(r"(.)\1{3,}")

# Garbled OCR syllable combinations
_NOISE_SYLLABLES = re.compile(
    r"[륙곰슨묻릉룬륨름륵몽룸뭬룬늘믄뉘녈][어은을룸름릉늙류늘는근]"
)


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
    """Correct OCR noise using LLM (EXAONE via Ollama).

    Args:
        text: OCR-extracted text with potential noise.
        ollama_client: OllamaClient instance with generate() method.

    Returns:
        Corrected text, or original if correction fails or is too short.
    """
    if not text or not needs_correction(text):
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

    except Exception as e:
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
