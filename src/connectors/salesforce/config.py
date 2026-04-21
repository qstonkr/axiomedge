"""Salesforce connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SalesforceConnectorConfig:
    """Resolved configuration for a Salesforce-backed data source.

    Attributes:
        auth_token: Connected App credentials JSON (instance_url + client_id +
            client_secret + refresh_token). shared SecretBox.
        soql: SOQL query — fetch 할 record set 정의.
            예: ``SELECT Id, Name, Description, Industry FROM Account WHERE LastModifiedDate >= LAST_N_DAYS:30``
        object_name: SObject 이름 (예: ``Account``, ``Opportunity``, ``Case``).
            doc_id 와 metadata 에 사용 — soql 의 FROM 절과 일치 권장.
        title_field: RawDocument.title 로 사용할 field (default ``Name``).
        body_fields: content 로 결합할 field list (default ``("Description",)``).
        api_version: Salesforce REST API version (default ``v60.0``).
        max_records: 최대 record 수 (default 500).
        name: human readable.
    """

    auth_token: str
    soql: str
    object_name: str
    title_field: str = "Name"
    body_fields: tuple[str, ...] = ("Description",)
    api_version: str = "v60.0"
    max_records: int = 500
    name: str = ""

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> SalesforceConnectorConfig:
        crawl_cfg = source.get("crawl_config") or {}
        token = str(crawl_cfg.get("auth_token") or "").strip()
        if not token:
            raise ValueError(
                "salesforce connector requires auth_token "
                "(Connected App credentials JSON, shared SecretBox)",
            )

        soql = str(crawl_cfg.get("soql") or "").strip()
        if not soql:
            raise ValueError(
                "salesforce connector requires crawl_config.soql "
                "(예: 'SELECT Id, Name, Description FROM Account LIMIT 500')",
            )

        object_name = str(crawl_cfg.get("object_name") or "").strip()
        if not object_name:
            raise ValueError(
                "salesforce connector requires crawl_config.object_name "
                "(예: 'Account', 'Opportunity', 'Case')",
            )

        raw_body = crawl_cfg.get("body_fields") or ("Description",)
        if isinstance(raw_body, str):
            raw_body = [s.strip() for s in raw_body.split(",") if s.strip()]
        body_fields = tuple(str(f).strip() for f in raw_body if str(f).strip())
        if not body_fields:
            body_fields = ("Description",)

        return cls(
            auth_token=token,
            soql=soql,
            object_name=object_name,
            title_field=str(crawl_cfg.get("title_field") or "Name"),
            body_fields=body_fields,
            api_version=str(crawl_cfg.get("api_version") or "v60.0"),
            max_records=int(crawl_cfg.get("max_records") or 500),
            name=str(source.get("name") or ""),
        )
