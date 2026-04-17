"""Glossary CSV import — parse, validate, enrich, and batch-insert glossary terms."""

from __future__ import annotations

import csv
import io
import logging
import unicodedata
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.stores.postgres.repositories.glossary import GlossaryRepository

from fastapi import UploadFile

from src.nlp.korean.morpheme_analyzer import get_analyzer
from src.nlp.korean.term_normalizer import TermNormalizer

logger = logging.getLogger(__name__)

# Korean column name mapping (P1-5)
_KO_COLUMN_MAP = {
    "물리명": "term",
    "논리명": "term_ko",
    "정의": "definition",
    "동의어": "synonyms",
    "약어": "abbreviations",
    "물리의미": "physical_meaning",
    "구성정보": "composition_info",
    "도메인명": "domain_name",
    "표준분류": "source",
    "데이터타입": "data_type",
    "데이터길이": "data_length",
    "데이터소수점": "data_decimal",
}

BATCH_SIZE = 500


async def import_csv(
    repo: GlossaryRepository,
    upload_files: list[UploadFile],
    encoding: str = "utf-8",
    kb_id: str = "global-standard",
) -> dict[str, Any]:
    """Import glossary terms from CSV files.

    Returns dict with success, imported, skipped, errors, etc.
    """
    total_imported = 0
    total_skipped = 0
    all_errors: list[str] = []
    word_count = 0
    term_count = 0

    for uf in upload_files:
        result = await _import_single_csv(repo, uf, encoding, kb_id)
        total_imported += result["imported"]
        total_skipped += result["skipped"]
        word_count += result["words"]
        term_count += result["terms"]
        all_errors.extend(result["errors"])

    return {
        "success": total_imported > 0,
        "imported": total_imported,
        "skipped": total_skipped,
        "files_processed": len(upload_files),
        "auto_detected_words": word_count,
        "auto_detected_terms": term_count,
        "errors": all_errors[:20],
    }


async def _import_single_csv(
    repo: GlossaryRepository, uf: UploadFile, encoding: str, kb_id: str,
) -> dict[str, Any]:
    """Process a single CSV file and return import stats."""
    morpheme_analyzer = get_analyzer()
    term_normalizer = TermNormalizer()
    fname = uf.filename or "unknown.csv"
    imported = 0
    skipped = 0
    words = 0
    terms = 0
    errors: list[str] = []

    try:
        content = await uf.read()
        text = content.decode(encoding)
        reader = csv.DictReader(io.StringIO(text))

        if not _validate_columns(reader.fieldnames, fname, errors):
            return {"imported": 0, "skipped": 0, "words": 0, "terms": 0, "errors": errors}

        batch: list[dict[str, Any]] = []
        for row_num, row in enumerate(reader, start=2):
            row = _map_korean_columns(row)
            term = row.get("term", "").strip()
            if not term:
                skipped += 1
                continue

            term = unicodedata.normalize("NFC", term)
            term = morpheme_analyzer.strip_particles(term)

            try:
                term_data = _build_term_data(row, term, kb_id, term_normalizer)
                if term_data["term_type"] == "word":
                    words += 1
                else:
                    terms += 1
                batch.append(term_data)

                if len(batch) >= BATCH_SIZE:
                    imported += await _flush_batch(repo, batch, fname, row_num, errors)
                    batch = []
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
                errors.append(f"{fname} Row {row_num}: {e}")

        if batch:
            imported += await _flush_batch(repo, batch, fname, "final", errors)
    except (RuntimeError, OSError, csv.Error, ValueError) as e:
        errors.append(f"{fname}: {e}")

    return {"imported": imported, "skipped": skipped, "words": words, "terms": terms, "errors": errors}


def _validate_columns(fieldnames: list[str] | None, fname: str, errors: list[str]) -> bool:
    """Check CSV has required columns."""
    if not fieldnames or "term" in fieldnames:
        return True
    if any(k in fieldnames for k in _KO_COLUMN_MAP):
        return True
    errors.append(f"{fname}: 필수 컬럼 'term' 또는 '물리명'이 없습니다. 발견된 컬럼: {fieldnames}")
    return False


def _map_korean_columns(row: dict[str, Any]) -> dict[str, Any]:
    """Map Korean column names to English keys."""
    return {_KO_COLUMN_MAP.get(k, k): v for k, v in row.items() if k is not None}


async def _flush_batch(
    repo: GlossaryRepository, batch: list[dict], fname: str, row_ref: Any, errors: list[str],
) -> int:
    """Save batch to repo, return count inserted."""
    try:
        return await repo.save_batch(batch)
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        errors.append(f"{fname} batch ending row {row_ref}: {e}")
        return 0


def _enrich_synonyms(
    row: dict[str, Any], term: str, synonyms: list[str],
) -> list[str]:
    """Auto-enrich synonyms from physical_meaning and term_ko."""
    physical_meaning = (row.get("physical_meaning", "") or "").strip()
    if physical_meaning:
        pm_lower = physical_meaning.lower()
        existing_lower = {s.lower() for s in synonyms}
        if pm_lower not in existing_lower and pm_lower != term.lower():
            synonyms.append(physical_meaning)

    term_ko = (row.get("term_ko", "") or "").strip()
    if term_ko and term_ko.lower() != term.lower():
        if term_ko.lower() not in {s.lower() for s in synonyms}:
            synonyms.append(term_ko)

    return synonyms


def _detect_term_type(row: dict[str, Any], term: str) -> str:
    """Auto-detect term_type from composition_info column."""
    composition = row.get("composition_info", "").strip()
    has_composition_col = "composition_info" in row or "구성정보" in row

    if composition:
        auto_term_type = "word" if len(composition.split()) <= 1 else "term"
    elif not has_composition_col:
        auto_term_type = "word" if "_" not in term and len(term) <= 10 else "term"
    else:
        auto_term_type = "word"

    return row.get("term_type", auto_term_type)


def _build_term_data(
    row: dict[str, Any], term: str, kb_id: str, term_normalizer: TermNormalizer,
) -> dict[str, Any]:
    """Build a glossary term dict from a parsed CSV row."""
    synonyms_raw = row.get("synonyms", "") or ""
    abbreviations_raw = row.get("abbreviations", "") or ""
    synonyms = [s.strip() for s in synonyms_raw.split(",") if s.strip()]
    abbreviations = [a.strip() for a in abbreviations_raw.split(",") if a.strip()]

    synonyms = _enrich_synonyms(row, term, synonyms)

    if term_normalizer.is_likely_abbreviation(term) and not abbreviations:
        abbreviations = [term]

    status = row.get("status", "pending")
    scope = row.get("scope", "global")
    if scope == "global":
        status = "approved"

    final_term_type = _detect_term_type(row, term)

    source_val = row.get("source", "").strip() or "csv_import"
    effective_kb_id = source_val if source_val != "csv_import" else row.get("kb_id", kb_id)

    return {
        "id": str(uuid.uuid4()),
        "kb_id": effective_kb_id,
        "term": term,
        "term_ko": row.get("term_ko", "") or "",
        "definition": row.get("definition", "") or "",
        "synonyms": synonyms,
        "abbreviations": abbreviations,
        "source": source_val,
        "status": status,
        "term_type": final_term_type,
        "scope": scope,
        "physical_meaning": row.get("physical_meaning", "") or "",
        "composition_info": row.get("composition_info", "") or "",
        "domain_name": row.get("domain_name", "") or "",
    }
