"""Distill toolchain pre-flight 검증.

핵심 보장:
1. ``preflight_toolchain()`` 가 env 미설정 시 ``ToolchainNotConfiguredError`` raise
2. ``trigger_build`` / ``reset_to_base_model`` 라우트가 toolchain 미구성 시
   400 + setup 안내 — **빌드 row 가 생성되지 않음**
3. quantize 가 빠진 steps 는 pre-flight skip (training-only)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# preflight_toolchain helper 단위
# ---------------------------------------------------------------------------


class TestPreflightHelper:
    def test_missing_both_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DISTILL_CONVERT_SCRIPT", raising=False)
        monkeypatch.delenv("DISTILL_QUANTIZE_BIN", raising=False)
        monkeypatch.delenv("DISTILL_ALLOW_PATH_FALLBACK", raising=False)

        from src.distill.quantizer import (
            ToolchainNotConfiguredError,
            preflight_toolchain,
        )
        with pytest.raises(ToolchainNotConfiguredError) as exc:
            preflight_toolchain()
        msg = str(exc.value)
        assert "DISTILL_CONVERT_SCRIPT" in msg
        assert "DISTILL_QUANTIZE_BIN" in msg
        assert "make setup-distill-toolchain" in msg

    def test_missing_only_quantize(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ):
        # convert script exists, quantize missing
        convert = tmp_path / "convert_hf_to_gguf.py"
        convert.write_text("#!/usr/bin/env python3\n")
        monkeypatch.setenv("DISTILL_CONVERT_SCRIPT", str(convert))
        monkeypatch.delenv("DISTILL_QUANTIZE_BIN", raising=False)
        monkeypatch.delenv("DISTILL_ALLOW_PATH_FALLBACK", raising=False)

        from src.distill.quantizer import (
            ToolchainNotConfiguredError,
            preflight_toolchain,
        )
        with pytest.raises(ToolchainNotConfiguredError) as exc:
            preflight_toolchain()
        msg = str(exc.value)
        # "env 누락:" 뒤의 missing list 만 체크 — _SETUP_HINT 안의 env var
        # 이름은 무시 (도움말 텍스트라 정상).
        missing_section = msg.split("env 누락:")[1].split(".")[0]
        assert "DISTILL_QUANTIZE_BIN" in missing_section
        assert "DISTILL_CONVERT_SCRIPT" not in missing_section

    def test_both_present_returns_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ):
        import os
        convert = tmp_path / "convert_hf_to_gguf.py"
        convert.write_text("#!/usr/bin/env python3\n")
        quantize = tmp_path / "llama-quantize"
        quantize.write_bytes(b"\x7fELF")  # fake binary
        os.chmod(quantize, 0o755)
        monkeypatch.setenv("DISTILL_CONVERT_SCRIPT", str(convert))
        monkeypatch.setenv("DISTILL_QUANTIZE_BIN", str(quantize))

        from src.distill.quantizer import preflight_toolchain
        c, q = preflight_toolchain()
        assert c == str(convert)
        assert q == str(quantize)


# ---------------------------------------------------------------------------
# 라우트 단위 — trigger_build / reset_to_base_model 가 사전 차단
# ---------------------------------------------------------------------------


class TestRoutePreflight:
    def test_trigger_build_blocks_when_toolchain_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("DISTILL_CONVERT_SCRIPT", raising=False)
        monkeypatch.delenv("DISTILL_QUANTIZE_BIN", raising=False)
        monkeypatch.delenv("DISTILL_ALLOW_PATH_FALLBACK", raising=False)

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.distill_builds import router

        app = FastAPI()
        app.include_router(router)

        repo = AsyncMock()
        repo.get_profile = AsyncMock(return_value={
            "name": "p1", "enabled": True, "base_model": "x", "search_group": "g",
        })
        repo.create_build = AsyncMock()
        repo.create_build_unique = AsyncMock()  # 호출되면 안 됨

        with patch(
            "src.api.routes.distill_builds._get_distill_repo", return_value=repo,
        ), patch(
            "src.api.routes.distill_builds._get_state",
            return_value={"distill_service": AsyncMock()},
        ):
            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/distill/builds",
                        json={"profile_name": "p1"},  # steps=None → quantize 포함
                    )
                    assert resp.status_code == 400
                    detail = resp.json()["detail"]
                    assert "toolchain" in detail.lower()
                    assert "make setup-distill-toolchain" in detail
            _run(_t())

        # 빌드 row 가 생성되지 않았는지 (고아 row 방지)
        repo.create_build_unique.assert_not_called()

    def test_reset_to_base_blocks_when_toolchain_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("DISTILL_CONVERT_SCRIPT", raising=False)
        monkeypatch.delenv("DISTILL_QUANTIZE_BIN", raising=False)
        monkeypatch.delenv("DISTILL_ALLOW_PATH_FALLBACK", raising=False)

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.distill_builds import router

        app = FastAPI()
        app.include_router(router)

        repo = AsyncMock()
        repo.get_profile = AsyncMock(return_value={"name": "p1", "base_model": "x"})
        repo.create_build = AsyncMock()
        repo.create_build_unique = AsyncMock()  # 호출되면 안 됨

        with patch(
            "src.api.routes.distill_builds._get_distill_repo", return_value=repo,
        ), patch(
            "src.api.routes.distill_builds._get_state",
            return_value={"distill_service": AsyncMock()},
        ):
            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/distill/builds/reset-to-base?profile_name=p1",
                    )
                    assert resp.status_code == 400
                    assert "toolchain" in resp.json()["detail"].lower()
            _run(_t())

        repo.create_build_unique.assert_not_called()

    def test_trigger_build_skips_preflight_when_no_quantize(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """학습-only 빌드는 toolchain 안 필요 — pre-flight skip."""
        monkeypatch.delenv("DISTILL_CONVERT_SCRIPT", raising=False)
        monkeypatch.delenv("DISTILL_QUANTIZE_BIN", raising=False)

        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from src.api.routes.distill_builds import router

        app = FastAPI()
        app.include_router(router)

        repo = AsyncMock()
        repo.get_profile = AsyncMock(return_value={
            "name": "p1", "enabled": True, "base_model": "x", "search_group": "g",
        })
        repo.create_build = AsyncMock()
        repo.create_build_unique = AsyncMock()
        distill_service = AsyncMock()
        distill_service.run_pipeline = AsyncMock()

        with patch(
            "src.api.routes.distill_builds._get_distill_repo", return_value=repo,
        ), patch(
            "src.api.routes.distill_builds._get_state",
            return_value={"distill_service": distill_service},
        ):
            async def _t():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test",
                ) as ac:
                    resp = await ac.post(
                        "/api/v1/distill/builds",
                        json={"profile_name": "p1", "steps": ["generate", "train"]},
                    )
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "pending"
            _run(_t())

        # quantize 안 들어간 steps → preflight 건너뛰고 정상 진행
        repo.create_build_unique.assert_called_once()
