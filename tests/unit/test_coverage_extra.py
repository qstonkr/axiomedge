"""Extra coverage tests for ingestion_gate, enhanced_similarity_matcher, ocr_corrector.

Targets: ingestion_gate (47 uncov), enhanced_similarity_matcher (132 uncov),
ocr_corrector (58 uncov).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

import pytest

from src.domain.models import RawDocument

# ===========================================================================
# IngestionGate
# ===========================================================================

from src.pipeline.ingestion_gate import (
    IngestionGate,
    GateAction,
    GateVerdict,
    GateResult,
    CheckResult,
    _ExactDedupIndex,
    _check_ig01_source_validation,
    _check_ig02_freshness,
    _check_ig03_content_validity,
    _check_ig04_lifecycle,
    _check_ig05_exact_dedup,
    _check_ig06_file_type_eligibility,
    _check_ig07_content_size_limit,
    _check_ig10_structure_quality,
    _check_ig11_language_detection,
    _check_ig12_snippet_detection,
)


def _make_doc(
    content="충분한 길이의 한국어 테스트 문서 내용입니다. 이것은 테스트를 위한 것입니다.",
    **kwargs,
) -> RawDocument:
    defaults = {
        "doc_id": "test-001",
        "title": "Test Doc",
        "source_uri": "file://test.pdf",
        "metadata": {"source_type": "file"},
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    return RawDocument(content=content, **defaults)


class TestExactDedupIndex:
    def test_first_add(self):
        idx = _ExactDedupIndex()
        is_dup, _ = idx.check_and_add("hash1", "doc1")
        assert is_dup is False

    def test_duplicate(self):
        idx = _ExactDedupIndex()
        idx.check_and_add("hash1", "doc1")
        is_dup, dup_of = idx.check_and_add("hash1", "doc2")
        assert is_dup is True
        assert dup_of == "doc1"


class TestIG01SourceValidation:
    def test_known_source(self):
        doc = _make_doc(metadata={"source_type": "confluence"})
        result = _check_ig01_source_validation(doc, "kb1")
        assert result.verdict == GateVerdict.PASS

    def test_missing_source(self):
        doc = _make_doc(metadata={})
        result = _check_ig01_source_validation(doc, "kb1")
        assert result.verdict == GateVerdict.FAIL

    def test_unknown_source(self):
        doc = _make_doc(metadata={"source_type": "unknown_system"})
        result = _check_ig01_source_validation(doc, "kb1")
        assert result.verdict == GateVerdict.WARN


class TestIG02Freshness:
    def test_fresh_doc(self):
        doc = _make_doc(updated_at=datetime.now(timezone.utc))
        result = _check_ig02_freshness(doc)
        assert result.verdict == GateVerdict.PASS

    def test_no_updated_at(self):
        doc = _make_doc(updated_at=None)
        result = _check_ig02_freshness(doc)
        assert result.verdict == GateVerdict.WARN

    def test_old_doc(self):
        old = datetime.now(timezone.utc) - timedelta(days=9999)
        doc = _make_doc(updated_at=old)
        result = _check_ig02_freshness(doc)
        assert result.verdict == GateVerdict.FAIL


class TestIG03ContentValidity:
    def test_valid_content(self):
        doc = _make_doc(content="x" * 500)
        result = _check_ig03_content_validity(doc)
        assert result.verdict == GateVerdict.PASS

    def test_empty_content(self):
        doc = _make_doc(content="")
        result = _check_ig03_content_validity(doc)
        assert result.verdict == GateVerdict.FAIL

    def test_short_content(self):
        doc = _make_doc(content="hi")
        result = _check_ig03_content_validity(doc)
        assert result.verdict in (GateVerdict.WARN, GateVerdict.FAIL)


class TestIG04Lifecycle:
    def test_active_doc(self):
        doc = _make_doc(metadata={"source_type": "file", "status": "active"})
        result = _check_ig04_lifecycle(doc)
        assert result.verdict == GateVerdict.PASS

    def test_archived_doc(self):
        doc = _make_doc(metadata={"source_type": "file", "status": "archived"})
        result = _check_ig04_lifecycle(doc)
        assert result.verdict == GateVerdict.FAIL

    def test_draft_doc(self):
        doc = _make_doc(metadata={"source_type": "file", "status": "draft"})
        result = _check_ig04_lifecycle(doc)
        assert result.verdict == GateVerdict.WARN

    def test_deprecated_keyword(self):
        doc = _make_doc(content="이 문서는 폐기 처리되었습니다.", metadata={"source_type": "file"})
        result = _check_ig04_lifecycle(doc)
        assert result.verdict == GateVerdict.FAIL


class TestIG05ExactDedup:
    def test_no_dup(self):
        idx = _ExactDedupIndex()
        doc = _make_doc()
        result = _check_ig05_exact_dedup(doc, idx)
        assert result.verdict == GateVerdict.PASS

    def test_duplicate(self):
        idx = _ExactDedupIndex()
        doc1 = _make_doc(content="same content")
        doc2 = _make_doc(content="same content", doc_id="test-002")
        _check_ig05_exact_dedup(doc1, idx)
        result = _check_ig05_exact_dedup(doc2, idx)
        assert result.verdict == GateVerdict.FAIL


class TestIG06FileType:
    def test_pdf(self):
        doc = _make_doc(metadata={"source_type": "file", "filename": "test.pdf"})
        result = _check_ig06_file_type_eligibility(doc)
        assert result.verdict == GateVerdict.PASS

    def test_exe(self):
        doc = _make_doc(metadata={"source_type": "file", "filename": "test.exe"})
        result = _check_ig06_file_type_eligibility(doc)
        assert result.verdict == GateVerdict.FAIL

    def test_no_filename(self):
        doc = _make_doc(metadata={"source_type": "file"})
        result = _check_ig06_file_type_eligibility(doc)
        assert result.verdict == GateVerdict.PASS

    def test_unknown_ext(self):
        doc = _make_doc(metadata={"source_type": "file", "filename": "test.xyz"})
        result = _check_ig06_file_type_eligibility(doc)
        assert result.verdict == GateVerdict.WARN

    def test_no_extension(self):
        doc = _make_doc(metadata={"source_type": "file", "filename": "README"})
        result = _check_ig06_file_type_eligibility(doc)
        assert result.verdict == GateVerdict.WARN


class TestIG07ContentSize:
    def test_within_limit(self):
        doc = _make_doc(content="a" * 1000)
        result = _check_ig07_content_size_limit(doc)
        assert result.verdict == GateVerdict.PASS


class TestIG10StructureQuality:
    def test_good_structure(self):
        content = "# Heading\n\nParagraph one with many words here.\n\nParagraph two with more words."
        doc = _make_doc(content=content * 20)
        result = _check_ig10_structure_quality(doc)
        assert result.verdict == GateVerdict.PASS

    def test_very_short(self):
        doc = _make_doc(content="ab")
        result = _check_ig10_structure_quality(doc)
        assert result.verdict == GateVerdict.FAIL


class TestIG11LanguageDetection:
    def test_korean(self):
        doc = _make_doc(content="한국어 테스트 문서입니다. 이것은 충분히 긴 내용을 포함하고 있습니다.")
        result = _check_ig11_language_detection(doc)
        assert result.verdict in (GateVerdict.PASS, GateVerdict.WARN)

    def test_too_short(self):
        doc = _make_doc(content="hi")
        result = _check_ig11_language_detection(doc)
        assert result.verdict == GateVerdict.SKIP


class TestIG12SnippetDetection:
    def test_sufficient(self):
        doc = _make_doc(content="a" * 500)
        result = _check_ig12_snippet_detection(doc)
        assert result.verdict == GateVerdict.PASS

    def test_too_short(self):
        doc = _make_doc(content="a")
        result = _check_ig12_snippet_detection(doc)
        assert result.verdict == GateVerdict.FAIL


class TestIngestionGateOrchestrator:
    def test_disabled(self):
        gate = IngestionGate(enabled=False)
        doc = _make_doc()
        result = gate.run_gates(doc, "kb1")
        assert result.action == GateAction.PROCEED
        assert result.checks == []

    def test_proceed_normal(self):
        gate = IngestionGate()
        doc = _make_doc(
            content="충분한 한국어 텍스트입니다. " * 50,
            metadata={"source_type": "file", "filename": "test.pdf"},
        )
        result = gate.run_gates(doc, "kb1")
        assert result.action in (GateAction.PROCEED, GateAction.HOLD)

    def test_quarantine_blocked_file(self):
        gate = IngestionGate()
        doc = _make_doc(
            content="a" * 500,
            metadata={"source_type": "file", "filename": "virus.exe"},
        )
        result = gate.run_gates(doc, "kb1")
        assert result.action == GateAction.QUARANTINE

    def test_reset_dedup(self):
        gate = IngestionGate()
        gate._dedup_index.check_and_add("hash1", "doc1")
        gate.reset_dedup_index()
        is_dup, _ = gate._dedup_index.check_and_add("hash1", "doc2")
        assert is_dup is False

    def test_gate_result_properties(self):
        gr = GateResult(
            action=GateAction.PROCEED,
            checks=[
                CheckResult("IG-01", "test", GateVerdict.PASS, "ok"),
                CheckResult("IG-02", "test", GateVerdict.WARN, "stale"),
                CheckResult("IG-03", "test", GateVerdict.FAIL, "empty"),
            ],
        )
        assert gr.passed_count == 1
        assert gr.warned_count == 1
        assert gr.failed_count == 1
        assert gr.is_blocked is False
        d = gr.to_dict()
        assert d["action"] == "proceed"


# ===========================================================================
# EnhancedSimilarityMatcher
# ===========================================================================

from src.search.enhanced_similarity_matcher import (
    EnhancedSimilarityMatcher,
    EnhancedMatcherConfig,
    MatchDecision,
    _try_strip_particle,
    _strip_particles as esm_strip_particles,
)


@dataclass
class _FakeTerm:
    term: str
    term_ko: str = ""
    synonyms: list[str] = field(default_factory=list)
    abbreviations: list[str] = field(default_factory=list)
    physical_meaning: str = ""
    term_type: str = "TERM"


class TestMatchDecision:
    def test_defaults(self):
        d = MatchDecision(zone="NEW_TERM")
        assert d.score == 0.0
        assert d.matched_term is None


class TestTryStripParticle:
    def test_strip(self):
        result, changed = _try_strip_particle("시스템에서", ["에서"])
        assert result == "시스템"
        assert changed is True

    def test_no_strip(self):
        result, changed = _try_strip_particle("시스템", ["에서"])
        assert changed is False

    def test_too_short_to_strip(self):
        result, changed = _try_strip_particle("서에서", ["에서"])
        # len("서") = 1, len("에서") = 2 -> 1 > 2 + 2 -> False
        assert changed is False


class TestEnhancedSimilarityMatcher:
    def test_load_standard_terms(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="GraphRAG", term_ko="그래프래그"),
            _FakeTerm(term="Knowledge Base", term_ko="지식베이스"),
        ]
        matcher.load_standard_terms(terms)
        assert matcher._loaded is True
        assert len(matcher._precomputed) == 2

    def test_load_with_synonyms(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(
                term="K8s",
                term_ko="쿠버네티스",
                synonyms=["Kubernetes"],
                abbreviations=["K8S"],
            ),
        ]
        matcher.load_standard_terms(terms)
        assert "kubernetes" in matcher._normalized_lookup or "k8s" in matcher._normalized_lookup

    def test_load_word_type(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="정산", term_ko="정산", term_type="WORD"),
            _FakeTerm(term="GraphRAG", term_ko="그래프래그", term_type="TERM"),
        ]
        matcher.load_standard_terms(terms)
        # WORD should be in _word_lookup but not in _precomputed
        assert len(matcher._precomputed) == 1  # only TERM

    def test_load_idempotent(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="Test")]
        matcher.load_standard_terms(terms)
        matcher.load_standard_terms(terms)  # second call should no-op
        assert len(matcher._precomputed) == 1

    def test_load_with_physical_meaning(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [
            _FakeTerm(term="DB", term_ko="데이터베이스", physical_meaning="Database"),
        ]
        matcher.load_standard_terms(terms)
        assert "database" in matcher._normalized_lookup

    async def test_match_exact(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="GraphRAG", term_ko="그래프래그")]
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="GraphRAG", term_ko="그래프래그")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "AUTO_MATCH"
        assert decision.match_type == "exact"

    async def test_match_normalized(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="GraphRAG", term_ko="그래프래그")]
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="graphrag")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "AUTO_MATCH"

    async def test_match_particle_stripped(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="시스템", term_ko="시스템")]
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="시스템에서", term_ko="시스템에서")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "AUTO_MATCH"
        assert decision.match_type == "particle"

    async def test_match_not_found(self):
        matcher = EnhancedSimilarityMatcher(
            config=EnhancedMatcherConfig(
                enable_rapidfuzz=False,
                enable_dense_search=False,
                enable_cross_encoder=False,
            ),
        )
        terms = [_FakeTerm(term="GraphRAG")]
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="완전히다른단어")
        decision = await matcher.match_enhanced(query)
        assert decision.zone in ("NEW_TERM", "REVIEW")

    async def test_match_empty_query(self):
        matcher = EnhancedSimilarityMatcher()
        matcher.load_standard_terms([_FakeTerm(term="Test")])
        query = _FakeTerm(term="")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "NEW_TERM"

    async def test_match_synonym(self):
        matcher = EnhancedSimilarityMatcher()
        terms = [_FakeTerm(term="K8s", synonyms=["쿠버네티스"])]
        matcher.load_standard_terms(terms)
        query = _FakeTerm(term="쿠버네티스")
        decision = await matcher.match_enhanced(query)
        assert decision.zone == "AUTO_MATCH"
        assert decision.match_type in ("exact", "synonym")


# ===========================================================================
# OCR Corrector
# ===========================================================================

from src.pipeline.ocr_corrector import noise_score, needs_correction, correct_ocr_text


class TestNoiseScore:
    def test_clean_text(self):
        score = noise_score("정상적인 한국어 텍스트입니다.")
        assert score < 0.1

    def test_empty(self):
        assert noise_score("") == 0.0

    def test_jamo_noise(self):
        score = noise_score("ㅎㅎㅎㅎㅎㅎㅎㅎㅎ")
        assert score > 0.3

    def test_repeat_noise(self):
        score = noise_score("============================")
        assert score > 0.1


class TestNeedsCorrection:
    def test_clean_text(self):
        assert needs_correction("정상적인 텍스트입니다.") is False

    def test_ocr_tagged_noisy(self):
        assert needs_correction("[OCR] ㅎㅎㅎㅎㅎㅎㅎㅎ") is True

    def test_very_noisy(self):
        assert needs_correction("ㅎㅎㅎㅎㅎㅎㅎㅎㅎㅎㅎ") is True


class TestCorrectOcrText:
    async def test_correction(self):
        mock_client = AsyncMock()
        mock_client.generate.return_value = "교정된 텍스트"
        result = await correct_ocr_text("[OCR] ㅎㅎ깨진텍스트", mock_client)
        assert isinstance(result, str)

    async def test_fallback_on_error(self):
        mock_client = AsyncMock()
        mock_client.generate.side_effect = Exception("LLM down")
        result = await correct_ocr_text("원본 텍스트", mock_client)
        assert result == "원본 텍스트"  # should return original
