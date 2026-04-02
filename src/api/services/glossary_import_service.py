"""Glossary CSV import — parse, validate, enrich, and batch-insert glossary terms."""

from __future__ import annotations

import csv
import io
import logging
import unicodedata
import uuid
from typing import Any

from fastapi import UploadFile

from src.nlp.morpheme_analyzer import get_analyzer
from src.nlp.term_normalizer import TermNormalizer

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
    repo: Any,
    upload_files: list[UploadFile],
    encoding: str = "utf-8",
    kb_id: str = "global-standard",
) -> dict[str, Any]:
    """Import glossary terms from CSV files.

    Returns dict with success, imported, skipped, errors, etc.
    """
    morpheme_analyzer = get_analyzer()
    term_normalizer = TermNormalizer()

    total_imported = 0
    total_skipped = 0
    all_errors: list[str] = []
    word_count = 0
    term_count = 0

    for uf in upload_files:
        fname = uf.filename or "unknown.csv"
        try:
            content = await uf.read()
            text = content.decode(encoding)
            reader = csv.DictReader(io.StringIO(text))

            if reader.fieldnames and "term" not in reader.fieldnames:
                has_korean_col = any(k in (reader.fieldnames or []) for k in _KO_COLUMN_MAP)
                if not has_korean_col:
                    all_errors.append(
                        f"{fname}: 필수 컬럼 'term' 또는 '물리명'이 없습니다. "
                        f"발견된 컬럼: {reader.fieldnames}"
                    )
                    continue

            batch: list[dict[str, Any]] = []

            for row_num, row in enumerate(reader, start=2):
                mapped_row: dict[str, Any] = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    mapped_row[_KO_COLUMN_MAP.get(k, k)] = v
                row = mapped_row

                term = row.get("term", "").strip()
                if not term:
                    total_skipped += 1
                    continue

                term = unicodedata.normalize("NFC", term)
                term = morpheme_analyzer.strip_particles(term)

                try:
                    term_data = _build_term_data(row, term, kb_id, term_normalizer)
                    if term_data["term_type"] == "word":
                        word_count += 1
                    else:
                        term_count += 1

                    batch.append(term_data)

                    if len(batch) >= BATCH_SIZE:
                        try:
                            inserted = await repo.save_batch(batch)
                            total_imported += inserted
                        except Exception as e:
                            all_errors.append(f"{fname} batch ending row {row_num}: {e}")
                        batch = []

                except Exception as e:
                    all_errors.append(f"{fname} Row {row_num}: {e}")

            if batch:
                try:
                    inserted = await repo.save_batch(batch)
                    total_imported += inserted
                except Exception as e:
                    all_errors.append(f"{fname} final batch: {e}")

        except Exception as e:
            all_errors.append(f"{fname}: {e}")

    return {
        "success": total_imported > 0,
        "imported": total_imported,
        "skipped": total_skipped,
        "files_processed": len(upload_files),
        "auto_detected_words": word_count,
        "auto_detected_terms": term_count,
        "errors": all_errors[:20],
    }


def _build_term_data(
    row: dict[str, Any], term: str, kb_id: str, term_normalizer: TermNormalizer,
) -> dict[str, Any]:
    """Build a glossary term dict from a parsed CSV row."""
    synonyms_raw = row.get("synonyms", "") or ""
    abbreviations_raw = row.get("abbreviations", "") or ""
    synonyms = [s.strip() for s in synonyms_raw.split(",") if s.strip()]
    abbreviations = [a.strip() for a in abbreviations_raw.split(",") if a.strip()]

    # Auto-enrich from physical_meaning
    physical_meaning = (row.get("physical_meaning", "") or "").strip()
    if physical_meaning:
        pm_lower = physical_meaning.lower()
        existing_lower = {s.lower() for s in synonyms}
        if pm_lower not in existing_lower and pm_lower != term.lower():
            synonyms.append(physical_meaning)

    # Auto-enrich: term_ko ↔ term bidirectional
    term_ko = (row.get("term_ko", "") or "").strip()
    if term_ko and term_ko.lower() != term.lower():
        if term_ko.lower() not in {s.lower() for s in synonyms}:
            synonyms.append(term_ko)

    if term_normalizer.is_likely_abbreviation(term) and not abbreviations:
        abbreviations = [term]

    status = row.get("status", "pending")
    scope = row.get("scope", "global")
    if scope == "global":
        status = "approved"

    # Auto-detect term_type from composition_info
    composition = row.get("composition_info", "").strip()
    has_composition_col = "composition_info" in row or "구성정보" in row

    if composition:
        auto_term_type = "word" if len(composition.split()) <= 1 else "term"
    elif not has_composition_col:
        auto_term_type = "word" if "_" not in term and len(term) <= 10 else "term"
    else:
        auto_term_type = "word"

    final_term_type = row.get("term_type", auto_term_type)

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
