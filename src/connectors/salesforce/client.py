"""Salesforce REST API client — async httpx + SOQL paging.

Bearer access_token (refresh 는 auth.refresh_access_token() 가 담당).
SOQL paging via ``nextRecordsUrl`` (응답에 있으면 다음 page).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class SalesforceAPIError(RuntimeError):
    """Salesforce API 호출 실패."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class SalesforceClient:
    """Salesforce REST API thin wrapper — query + nextRecordsUrl paging."""

    def __init__(
        self,
        instance_url: str,
        access_token: str,
        *,
        api_version: str = "v60.0",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not access_token:
            raise ValueError("SalesforceClient requires non-empty access_token")
        if not instance_url:
            raise ValueError("SalesforceClient requires non-empty instance_url")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._api_path = f"/services/data/{api_version}"
        self._client = httpx.AsyncClient(
            base_url=instance_url, headers=self._headers, timeout=timeout,
        )

    async def __aenter__(self) -> SalesforceClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(2):
            try:
                resp = await self._client.get(path, **kwargs)
            except httpx.TimeoutException as e:
                raise SalesforceAPIError(f"timeout: {e}") from e
            except httpx.RequestError as e:
                raise SalesforceAPIError(f"network error: {e}") from e

            if resp.status_code == 503 and attempt == 0:
                # Salesforce 가 가끔 503 maintenance — 짧게 retry
                await asyncio.sleep(2.0)
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
                payload = []
            # Salesforce 에러는 list of {message, errorCode}
            if isinstance(payload, list) and payload:
                msg = "; ".join(
                    str(e.get("message") or "") for e in payload if isinstance(e, dict)
                )
            else:
                msg = resp.text[:200]
            raise SalesforceAPIError(
                f"salesforce {resp.status_code}: {msg}", status=resp.status_code,
            )
        raise SalesforceAPIError("salesforce: max retries exceeded")

    async def query(self, soql: str) -> AsyncIterator[dict[str, Any]]:
        """SOQL query — nextRecordsUrl 따라가며 paging. 모든 record yield."""
        path = f"{self._api_path}/query"
        # 첫 page
        page = await self._get(path, params={"q": soql})
        while True:
            for rec in page.get("records") or []:
                yield rec
            if page.get("done"):
                break
            next_url = page.get("nextRecordsUrl")
            if not next_url:
                break
            # nextRecordsUrl 은 ``/services/data/v60.0/query/01g...`` — base 그대로.
            page = await self._get(next_url)
