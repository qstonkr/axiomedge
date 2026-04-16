"""Connector plugin registry — @register_connector decorator + factory.

새로운 데이터 소스 커넥터를 추가할 때 decorator 한 줄로 등록.

Usage:

    # 커넥터 등록 (connector 모듈에서)
    @register_connector("notion")
    class NotionConnector:
        async def crawl(self, config: dict) -> list[RawDocument]: ...

    @register_connector("confluence")
    class ConfluenceConnector:
        async def crawl(self, config: dict) -> list[RawDocument]: ...

    # 팩토리 사용 (data_source_sync 등에서)
    connector = create_connector("confluence", config=crawler_config)
    docs = await connector.crawl(config)

현재 등록된 커넥터:
    - "confluence" — src.connectors.confluence
    - "file_upload" — src.connectors.file_upload
    - "git" — src.connectors.git.connector
    - "crawl_result" — src.connectors.crawl_result
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

CONNECTOR_REGISTRY: dict[str, type] = {}


@runtime_checkable
class IConnector(Protocol):
    """Connector interface — 외부 데이터 소스에서 문서를 가져오는 인터페이스."""

    async def crawl(self, config: dict[str, Any]) -> list[Any]:
        """데이터 소스에서 문서를 크롤링/추출."""
        ...  # pragma: no cover


def register_connector(name: str):
    """Connector 등록 decorator.

    Usage::

        @register_connector("notion")
        class NotionConnector:
            async def crawl(self, config): ...
    """
    def decorator(cls: type) -> type:
        if name in CONNECTOR_REGISTRY:
            logger.warning(
                "Connector '%s' already registered (%s), overwriting with %s",
                name, CONNECTOR_REGISTRY[name].__name__, cls.__name__,
            )
        CONNECTOR_REGISTRY[name] = cls
        logger.debug("Registered connector: %s -> %s", name, cls.__name__)
        return cls
    return decorator


def create_connector(name: str, **kwargs: Any) -> Any:
    """Registry에서 커넥터를 생성.

    Args:
        name: 등록된 커넥터 이름 (e.g. "confluence", "notion")
        **kwargs: 커넥터 생성자 인자

    Returns:
        커넥터 인스턴스

    Raises:
        ValueError: 미등록 커넥터
    """
    cls = CONNECTOR_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(CONNECTOR_REGISTRY.keys())) or "(none)"
        raise ValueError(
            f"Unknown connector: '{name}'. Available: {available}"
        )
    return cls(**kwargs)


def list_connectors() -> list[str]:
    """등록된 커넥터 이름 목록."""
    return sorted(CONNECTOR_REGISTRY.keys())
