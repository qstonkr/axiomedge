"""Unit tests for src/api/services/ — glossary_import_service, trust_score_calculator."""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# glossary_import_service.import_csv
# ===========================================================================
class TestImportCsv:
    def _make_upload(self, content: str, filename: str = "test.csv"):
        uf = AsyncMock()
        uf.filename = filename
        uf.read = AsyncMock(return_value=content.encode("utf-8"))
        return uf

    def test_basic_import(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        repo.save_batch = AsyncMock(return_value=2)

        csv_content = "term,term_ko,definition\nserver,서버,서버입니다\nnetwork,네트워크,네트워크입니다"
        uf = self._make_upload(csv_content)

        async def _go():
            result = await import_csv(repo, [uf])
            assert result["success"] is True
            assert result["imported"] == 2
            repo.save_batch.assert_awaited()

        _run(_go())

    def test_korean_columns(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        repo.save_batch = AsyncMock(return_value=1)

        csv_content = "물리명,논리명,정의\nSRV_NM,서버명,서버 이름"
        uf = self._make_upload(csv_content)

        async def _go():
            result = await import_csv(repo, [uf])
            assert result["success"] is True
            assert result["imported"] == 1

        _run(_go())

    def test_missing_columns(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        csv_content = "foo,bar\n1,2"
        uf = self._make_upload(csv_content)

        async def _go():
            result = await import_csv(repo, [uf])
            assert result["success"] is False
            assert len(result["errors"]) > 0

        _run(_go())

    def test_empty_terms_skipped(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        repo.save_batch = AsyncMock(return_value=1)

        csv_content = "term,definition\n,empty\nvalid,has content"
        uf = self._make_upload(csv_content)

        async def _go():
            result = await import_csv(repo, [uf])
            assert result["skipped"] == 1

        _run(_go())

    def test_batch_error(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        repo.save_batch = AsyncMock(side_effect=RuntimeError("db err"))

        csv_content = "term,definition\ntest,content"
        uf = self._make_upload(csv_content)

        async def _go():
            result = await import_csv(repo, [uf])
            assert result["success"] is False
            assert len(result["errors"]) > 0

        _run(_go())

    def test_multiple_files(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        repo.save_batch = AsyncMock(return_value=1)

        uf1 = self._make_upload("term,definition\nterm1,def1", "file1.csv")
        uf2 = self._make_upload("term,definition\nterm2,def2", "file2.csv")

        async def _go():
            result = await import_csv(repo, [uf1, uf2])
            assert result["files_processed"] == 2
            assert result["imported"] == 2

        _run(_go())

    def test_decode_error(self):
        from src.api.services.glossary_import_service import import_csv

        repo = AsyncMock()
        uf = AsyncMock()
        uf.filename = "bad.csv"
        uf.read = AsyncMock(return_value=b"\xff\xfe\x00invalid")

        async def _go():
            result = await import_csv(repo, [uf], encoding="ascii")
            assert len(result["errors"]) > 0

        _run(_go())


# ===========================================================================
# glossary_import_service._build_term_data
# ===========================================================================
class TestBuildTermData:
    def test_build_basic(self):
        from src.api.services.glossary_import_service import _build_term_data
        from src.nlp.korean.term_normalizer import TermNormalizer

        normalizer = TermNormalizer()
        row = {"term_ko": "서버명", "definition": "서버 이름", "synonyms": "srv,svr", "abbreviations": ""}
        result = _build_term_data(row, "SRV_NM", "global-standard", normalizer)
        assert result["term"] == "SRV_NM"
        assert "srv" in result["synonyms"]
        assert result["scope"] == "global"
        assert result["status"] == "approved"  # global scope -> approved

    def test_build_with_physical_meaning(self):
        from src.api.services.glossary_import_service import _build_term_data
        from src.nlp.korean.term_normalizer import TermNormalizer

        normalizer = TermNormalizer()
        row = {"physical_meaning": "server name", "term_ko": "", "synonyms": "", "abbreviations": ""}
        result = _build_term_data(row, "SRV", "kb1", normalizer)
        assert "server name" in result["synonyms"]

    def test_build_auto_detect_word(self):
        from src.api.services.glossary_import_service import _build_term_data
        from src.nlp.korean.term_normalizer import TermNormalizer

        normalizer = TermNormalizer()
        row = {"composition_info": "단일", "synonyms": "", "abbreviations": ""}
        result = _build_term_data(row, "서버", "kb1", normalizer)
        assert result["term_type"] == "word"

    def test_build_auto_detect_term(self):
        from src.api.services.glossary_import_service import _build_term_data
        from src.nlp.korean.term_normalizer import TermNormalizer

        normalizer = TermNormalizer()
        row = {"composition_info": "서버 이름 코드", "synonyms": "", "abbreviations": ""}
        result = _build_term_data(row, "SRV_NM_CD", "kb1", normalizer)
        assert result["term_type"] == "term"

    def test_build_source_from_csv(self):
        from src.api.services.glossary_import_service import _build_term_data
        from src.nlp.korean.term_normalizer import TermNormalizer

        normalizer = TermNormalizer()
        row = {"source": "custom_source", "synonyms": "", "abbreviations": ""}
        result = _build_term_data(row, "term1", "global-standard", normalizer)
        assert result["source"] == "custom_source"
        assert result["kb_id"] == "custom_source"


# ===========================================================================
# trust_score_calculator.calculate_kb_trust_scores
# ===========================================================================
class TestCalculateKbTrustScores:
    def test_calculate_success(self):
        from src.api.services.trust_score_calculator import calculate_kb_trust_scores

        trust_repo = AsyncMock()
        trust_repo.save = AsyncMock()

        async def _go():
            with patch("src.api.services.trust_score_calculator.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                # First call returns points, second returns empty
                resp1 = MagicMock()
                resp1.status_code = 200
                resp1.json.return_value = {
                    "result": {
                        "points": [
                            {"payload": {"doc_id": "d1", "quality_score": 80, "owner": "user1",
                                         "l1_category": "IT", "source_uri": "/path", "ingested_at": "2026-03-01T00:00:00Z",
                                         "source_type": "file"}},
                            {"payload": {"doc_id": "d2", "quality_score": 50, "owner": "",
                                         "l1_category": "기타", "source_uri": "", "ingested_at": "",
                                         "source_type": "file"}},
                        ],
                        "next_page_offset": None,
                    }
                }
                mock_client.post = AsyncMock(return_value=resp1)
                mock_client_cls.return_value = mock_client

                result = await calculate_kb_trust_scores("kb1", trust_repo, "kb_kb1")
                assert result["success"] is True
                assert result["documents_processed"] == 2
                assert result["scores_saved"] == 2

        _run(_go())

    def test_calculate_qdrant_error(self):
        from src.api.services.trust_score_calculator import calculate_kb_trust_scores

        trust_repo = AsyncMock()

        async def _go():
            with patch("src.api.services.trust_score_calculator.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                resp = MagicMock()
                resp.status_code = 500
                mock_client.post = AsyncMock(return_value=resp)
                mock_client_cls.return_value = mock_client

                result = await calculate_kb_trust_scores("kb1", trust_repo, "kb_kb1")
                assert result["success"] is True
                assert result["documents_processed"] == 0

        _run(_go())

    def test_calculate_repo_save_error(self):
        from src.api.services.trust_score_calculator import calculate_kb_trust_scores

        trust_repo = AsyncMock()
        trust_repo.save = AsyncMock(side_effect=RuntimeError("db err"))

        async def _go():
            with patch("src.api.services.trust_score_calculator.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "result": {
                        "points": [
                            {"payload": {"doc_id": "d1", "quality_score": 80, "source_uri": "/path",
                                         "ingested_at": "2025-01-01T00:00:00Z", "source_type": "file"}},
                        ],
                        "next_page_offset": None,
                    }
                }
                mock_client.post = AsyncMock(return_value=resp)
                mock_client_cls.return_value = mock_client

                result = await calculate_kb_trust_scores("kb1", trust_repo, "kb_kb1")
                assert result["errors"] == 1

        _run(_go())

    def test_calculate_freshness_tiers(self):
        """Test different freshness score tiers based on document age."""
        from src.api.services.trust_score_calculator import calculate_kb_trust_scores
        from datetime import datetime, timezone, timedelta

        trust_repo = AsyncMock()
        trust_repo.save = AsyncMock()

        now = datetime.now(timezone.utc)
        dates = [
            (now - timedelta(days=10)).isoformat(),   # <30d -> high
            (now - timedelta(days=60)).isoformat(),   # 30-90d
            (now - timedelta(days=120)).isoformat(),  # 90-180d
            (now - timedelta(days=365)).isoformat(),  # >180d
        ]
        points = [
            {"payload": {"doc_id": f"d{i}", "quality_score": 70, "source_uri": "/p",
                         "ingested_at": d, "source_type": "file"}}
            for i, d in enumerate(dates)
        ]

        async def _go():
            with patch("src.api.services.trust_score_calculator.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"result": {"points": points, "next_page_offset": None}}
                mock_client.post = AsyncMock(return_value=resp)
                mock_client_cls.return_value = mock_client

                result = await calculate_kb_trust_scores("kb1", trust_repo, "kb_kb1")
                assert result["scores_saved"] == 4

        _run(_go())

    def test_calculate_exception(self):
        from fastapi import HTTPException
        from src.api.services.trust_score_calculator import calculate_kb_trust_scores

        trust_repo = AsyncMock()

        async def _go():
            with patch("src.api.services.trust_score_calculator.httpx.AsyncClient", side_effect=RuntimeError("conn")):
                with pytest.raises(HTTPException) as exc_info:
                    await calculate_kb_trust_scores("kb1", trust_repo, "kb_kb1")
                assert exc_info.value.status_code == 500

        _run(_go())
