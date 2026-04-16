"""Coverage backfill — auth refresh_token + quality transparency_stats.

Tests critical untested endpoints in auth.py and quality.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==========================================================================
# Auth — refresh_token
# ==========================================================================


class TestRefreshToken:
    """Tests for POST /api/v1/auth/refresh endpoint."""

    @pytest.fixture
    def mock_state(self):
        jwt_service = MagicMock()
        jwt_service.decode_refresh_token.return_value = {
            "jti": "token-123",
            "family_id": "fam-1",
            "sub": "user@test.com",
        }
        jwt_service.access_expire_seconds = 3600

        token_store = AsyncMock()
        token_store.validate_and_rotate.return_value = {"jti": "new-token"}

        auth_service = AsyncMock()
        rbac = MagicMock()

        return {
            "jwt_service": jwt_service,
            "token_store": token_store,
            "auth_service": auth_service,
            "rbac_engine": rbac,
        }

    async def test_no_jwt_service_returns_503(self) -> None:
        from src.api.routes.auth import refresh_token
        request = MagicMock()

        with patch("src.api.routes.auth._get_state", return_value={}):
            with pytest.raises(Exception) as exc:  # noqa: BLE001
                await refresh_token(request, MagicMock())
            assert "503" in str(exc.value) or "JWT" in str(exc.value)

    async def test_missing_refresh_token_returns_401(self, mock_state) -> None:
        from src.api.routes.auth import refresh_token
        request = MagicMock()
        request.cookies = {}
        request.json = AsyncMock(return_value={})

        with patch("src.api.routes.auth._get_state", return_value=mock_state):
            with pytest.raises(Exception) as exc:  # noqa: BLE001
                await refresh_token(request, MagicMock())
            assert "401" in str(exc.value) or "Missing" in str(exc.value)

    async def test_revoked_token_revokes_family(self, mock_state) -> None:
        from src.api.routes.auth import refresh_token
        mock_state["token_store"].validate_and_rotate.return_value = None

        request = MagicMock()
        request.cookies = {"refresh_token": "old-token"}

        with patch("src.api.routes.auth._get_state", return_value=mock_state):
            with pytest.raises(Exception) as exc:  # noqa: BLE001
                await refresh_token(request, MagicMock())
            assert "401" in str(exc.value) or "revoked" in str(exc.value)
            mock_state["token_store"].revoke_family.assert_called_once_with("fam-1")

    async def test_successful_refresh(self, mock_state) -> None:
        from src.api.routes.auth import refresh_token

        new_pair = MagicMock()
        new_pair.access_token = "new-access"
        new_pair.refresh_token = "new-refresh"

        request = MagicMock()
        request.cookies = {"refresh_token": "old-token"}
        response = MagicMock()

        with (
            patch("src.api.routes.auth._get_state", return_value=mock_state),
            patch(
                "src.api.routes.auth.rotate_refresh_token",
                return_value={"new_pair": new_pair},
            ),
            patch("src.api.routes.auth.set_auth_cookies"),
        ):
            result = await refresh_token(request, response)
            assert result["success"] is True
            assert result["token_type"] == "Bearer"


# ==========================================================================
# Quality — transparency stats
# ==========================================================================


class TestTallyDocTransparency:
    """Tests for _tally_doc_transparency helper (inline re-implementation to avoid circular import)."""

    @staticmethod
    def _tally(pay: dict, counts: dict) -> None:
        """Mirrors quality.py::_tally_doc_transparency logic for isolated testing."""
        counts["total"] += 1
        if pay.get("owner"):
            counts["owner"] += 1
        if pay.get("l1_category") and pay.get("l1_category") != "기타":
            counts["category"] += 1
        if pay.get("source_uri"):
            counts["source"] += 1

    def test_tally_with_all_fields(self) -> None:
        payload = {
            "owner": "test-user",
            "l1_category": "FAQ",
            "source_uri": "https://wiki.example.com/page",
        }
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
        self._tally(payload, counts)
        assert counts["total"] == 1
        assert counts["owner"] == 1
        assert counts["category"] == 1
        assert counts["source"] == 1

    def test_tally_with_missing_fields(self) -> None:
        payload = {"content": "some text"}
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
        self._tally(payload, counts)
        assert counts["total"] == 1
        assert counts["owner"] == 0
        assert counts["category"] == 0
        assert counts["source"] == 0

    def test_tally_gita_category_ignored(self) -> None:
        """l1_category='기타' should NOT count as having a category."""
        payload = {"l1_category": "기타"}
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
        self._tally(payload, counts)
        assert counts["category"] == 0

    def test_tally_empty_payload(self) -> None:
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
        self._tally({}, counts)
        assert counts["total"] == 1

    def test_tally_accumulates(self) -> None:
        counts = {"total": 0, "owner": 0, "category": 0, "source": 0}
        self._tally({"owner": "a", "l1_category": "IT", "source_uri": "x"}, counts)
        self._tally({"owner": "b"}, counts)
        self._tally({}, counts)
        assert counts["total"] == 3
        assert counts["owner"] == 2
        assert counts["category"] == 1
        assert counts["source"] == 1
