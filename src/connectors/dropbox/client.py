"""Dropbox API v2 client — async httpx + cursor paging.

Dropbox API 는 모든 endpoint POST + JSON body. download endpoint 는
별도 ``content.dropboxapi.com`` 호스트.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RPC_BASE = "https://api.dropboxapi.com/2"
_CONTENT_BASE = "https://content.dropboxapi.com/2"
_DEFAULT_TIMEOUT = 60.0


class DropboxAPIError(RuntimeError):
    """Dropbox API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class DropboxClient:
    """Dropbox API v2 thin wrapper — list_folder + download."""

    def __init__(
        self, auth_token: str, *, timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not auth_token:
            raise ValueError("DropboxClient requires non-empty auth_token")
        self._token = auth_token
        self._timeout = timeout
        self._rpc = httpx.AsyncClient(
            base_url=_RPC_BASE, timeout=timeout,
            headers={
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> DropboxClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._rpc.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                resp = await self._rpc.post(path, json=body)
            except httpx.TimeoutException as e:
                raise DropboxAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise DropboxAPIError(f"network error: {e}") from e

            if resp.status_code == 429 and attempt == 0:
                # Dropbox 응답 body 의 ``error.retry_after`` 사용.
                try:
                    err = resp.json().get("error") or {}
                    wait = float(err.get("retry_after", 1))
                except (ValueError, TypeError):
                    wait = 1.0
                logger.warning("dropbox rate-limited, sleeping %.1fs", wait)
                await asyncio.sleep(wait)
                continue

            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {}

            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            summary = str(payload.get("error_summary") or resp.text[:200])
            raise DropboxAPIError(
                f"dropbox {resp.status_code}: {summary}", status=resp.status_code,
            )
        raise DropboxAPIError("dropbox: max retries exceeded")

    async def list_folder(
        self, path: str, *, recursive: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """``files/list_folder`` + cursor continuation. 모든 entry yield."""
        body: dict[str, Any] = {"path": path, "recursive": recursive, "limit": 2000}
        page = await self._post("/files/list_folder", body)
        for entry in page.get("entries") or []:
            yield entry
        while page.get("has_more"):
            cursor = page.get("cursor", "")
            if not cursor:
                break
            page = await self._post("/files/list_folder/continue", {"cursor": cursor})
            for entry in page.get("entries") or []:
                yield entry

    async def download(self, path: str) -> bytes:
        """``files/download`` — Dropbox-API-Arg header 로 path 전달."""
        url = f"{_CONTENT_BASE}/files/download"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Dropbox-API-Arg": json.dumps({"path": path}),
        }
        async with httpx.AsyncClient(timeout=120.0) as http:
            try:
                resp = await http.post(url, headers=headers)
            except httpx.RequestError as e:
                raise DropboxAPIError(f"download network error: {e}") from e
        if resp.status_code != 200:
            raise DropboxAPIError(
                f"dropbox download {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )
        return resp.content
