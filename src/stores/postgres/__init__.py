"""Knowledge Local - Database Layer.

PostgreSQL-backed persistence for all knowledge metadata.
"""

from src.stores.postgres.models import KnowledgeBase, RegistryBase
from src.stores.postgres.session import (
    create_async_session_factory,
    get_knowledge_session_maker,
    to_async_database_url,
)

__all__ = [
    "KnowledgeBase",
    "RegistryBase",
    "create_async_session_factory",
    "get_knowledge_session_maker",
    "to_async_database_url",
]
