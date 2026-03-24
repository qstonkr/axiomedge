"""Knowledge Local - Database Layer.

PostgreSQL-backed persistence for all knowledge metadata.
"""

from src.database.models import KnowledgeBase, RegistryBase
from src.database.session import (
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
