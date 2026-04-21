"""Google Workspace connectors — Drive / Sheets / Gmail.

각 connector 의 config validation + helper 단위 + auth resolver 분기.
실제 Google API 호출 X — token resolver 의 service account 분기와 raw token
분기 분리 검증 + grid → markdown / Gmail body 추출 helper 단위.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Catalog meta sync — 3개 모두 SHARED set 안에 있는지
# ---------------------------------------------------------------------------


class TestCatalogMetaSync:
    def test_google_connectors_in_shared_set(self):
        from src.connectors.catalog_meta import (
            SHARED_TOKEN_CONNECTORS,
            is_shared_token_connector,
            is_user_self_service,
        )
        for ct in ("google_drive", "google_sheets", "gmail"):
            assert ct in SHARED_TOKEN_CONNECTORS, f"{ct} missing"
            assert is_shared_token_connector(ct)
            assert is_user_self_service(ct)


# ---------------------------------------------------------------------------
# Google auth resolver
# ---------------------------------------------------------------------------


class TestGoogleAuth:
    def test_raw_access_token_passthrough(self):
        from src.connectors._google.auth import resolve_access_token
        out = _run(resolve_access_token("ya29.fake-raw-token", ["scope-x"]))
        assert out == "ya29.fake-raw-token"

    def test_empty_token_raises(self):
        from src.connectors._google.auth import (
            GoogleAuthError,
            resolve_access_token,
        )
        with pytest.raises(GoogleAuthError, match="비어있음"):
            _run(resolve_access_token("", ["scope-x"]))

    def test_service_account_recognized_but_jwt_signed(self, monkeypatch):
        """SA JSON 인식 분기 — 실제 network 호출은 mock httpx 로 차단."""
        from src.connectors._google import auth as auth_mod

        sa = {
            "type": "service_account",
            "client_email": "sa@p.iam.gserviceaccount.com",
            # 실제 RSA private key — pytest 가 서명 가능해야 함. 짧은 fixture
            # 으로 cryptography 가 만들어주는 RSA-2048 PEM.
            "private_key": _generate_test_pem(),
        }

        # token endpoint 호출 가로채서 200 response 흉내
        captured = {}

        class _FakeResponse:
            status_code = 200
            def json(self):
                return {"access_token": "exchanged-token"}

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, data=None):
                captured["url"] = url
                captured["data"] = data
                return _FakeResponse()

        monkeypatch.setattr(auth_mod.httpx, "AsyncClient", _FakeClient)

        token = _run(auth_mod.resolve_access_token(
            json.dumps(sa), ["scope-a", "scope-b"],
        ))
        assert token == "exchanged-token"
        assert "oauth2.googleapis.com" in captured["url"]
        assert captured["data"]["grant_type"].startswith("urn:ietf:params")
        assert "assertion" in captured["data"]


def _generate_test_pem() -> str:
    """One-shot RSA-2048 PEM — auth 단위 테스트용."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


# ---------------------------------------------------------------------------
# Google Drive config
# ---------------------------------------------------------------------------


class TestGoogleDriveConfig:
    def test_missing_token_raises(self):
        from src.connectors.google_drive.config import GoogleDriveConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            GoogleDriveConnectorConfig.from_source({"crawl_config": {}})

    def test_default_root_folder(self):
        from src.connectors.google_drive.config import GoogleDriveConnectorConfig
        cfg = GoogleDriveConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk"},
        })
        assert cfg.folder_id == "root"
        assert cfg.recursive is True

    def test_custom_mime_types_normalized(self):
        from src.connectors.google_drive.config import GoogleDriveConnectorConfig
        cfg = GoogleDriveConnectorConfig.from_source({
            "crawl_config": {
                "auth_token": "tk",
                "include_mime_types": "application/PDF, text/PLAIN",
            },
        })
        assert "application/pdf" in cfg.include_mime_types
        assert "text/plain" in cfg.include_mime_types


# ---------------------------------------------------------------------------
# Google Sheets — config + values → markdown
# ---------------------------------------------------------------------------


class TestGoogleSheetsConfig:
    def test_missing_spreadsheet_ids_raises(self):
        from src.connectors.google_sheets.config import GoogleSheetsConnectorConfig
        with pytest.raises(ValueError, match="spreadsheet_ids"):
            GoogleSheetsConnectorConfig.from_source({
                "crawl_config": {"auth_token": "tk"},
            })

    def test_string_ids_split_on_comma(self):
        from src.connectors.google_sheets.config import GoogleSheetsConnectorConfig
        cfg = GoogleSheetsConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk", "spreadsheet_ids": "a, b , c"},
        })
        assert cfg.spreadsheet_ids == ("a", "b", "c")


class TestSheetsToMarkdown:
    def test_simple_grid(self):
        from src.connectors.google_sheets.connector import _values_to_markdown
        out = _values_to_markdown(
            [["name", "qty"], ["apple", "3"], ["banana", "5"]],
            max_rows=100, max_cols=10,
        )
        assert "| name | qty |" in out
        assert "| --- | --- |" in out
        assert "| apple | 3 |" in out

    def test_pipe_in_cell_escaped(self):
        from src.connectors.google_sheets.connector import _values_to_markdown
        out = _values_to_markdown([["a|b", "c"]], max_rows=10, max_cols=10)
        assert "a\\|b" in out

    def test_max_cols_truncation(self):
        from src.connectors.google_sheets.connector import _values_to_markdown
        out = _values_to_markdown(
            [["a", "b", "c", "d", "e"]],
            max_rows=10, max_cols=2,
        )
        # 2개 col 만 — c/d/e 빠짐
        assert "| a | b |" in out
        assert " c " not in out


# ---------------------------------------------------------------------------
# Gmail — config + body 추출
# ---------------------------------------------------------------------------


class TestGmailConfig:
    def test_missing_token_raises(self):
        from src.connectors.gmail.config import GmailConnectorConfig
        with pytest.raises(ValueError, match="auth_token"):
            GmailConnectorConfig.from_source({"crawl_config": {}})

    def test_default_user_me(self):
        from src.connectors.gmail.config import GmailConnectorConfig
        cfg = GmailConnectorConfig.from_source({
            "crawl_config": {"auth_token": "tk"},
        })
        assert cfg.user_id == "me"


class TestGmailBodyExtract:
    def _b64(self, text: str) -> str:
        return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")

    def test_plain_part_preferred(self):
        from src.connectors.gmail.connector import _extract_body
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": self._b64("plain hello")}},
                {"mimeType": "text/html", "body": {"data": self._b64("<p>html hi</p>")}},
            ],
        }
        assert _extract_body(payload) == "plain hello"

    def test_html_fallback_stripped(self):
        from src.connectors.gmail.connector import _extract_body
        payload = {
            "mimeType": "text/html",
            "body": {"data": self._b64("<p>html <b>bold</b></p>")},
        }
        out = _extract_body(payload)
        assert "<p>" not in out
        assert "html" in out and "bold" in out

    def test_nested_multipart(self):
        from src.connectors.gmail.connector import _extract_body
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": self._b64("nested plain")}},
                    ],
                },
            ],
        }
        assert _extract_body(payload) == "nested plain"

    def test_empty_payload_returns_empty(self):
        from src.connectors.gmail.connector import _extract_body
        assert _extract_body({}) == ""
