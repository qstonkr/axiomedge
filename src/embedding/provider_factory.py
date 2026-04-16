"""Embedding Provider Factory — facade.

실제 구현은 ``src/providers/embedding.py`` 로 이동됨.
기존 import 경로 유지를 위한 re-export.
"""

from src.providers.embedding import (  # noqa: F401
    create_embedding_provider,
)
