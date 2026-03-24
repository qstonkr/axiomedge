"""LLM clients for local knowledge generation."""

from .ollama_client import OllamaClient, OllamaConfig
from .prompts import OWNER_QUERY_PROMPT, RAG_PROMPT, SYSTEM_PROMPT

__all__ = [
    "OllamaClient",
    "OllamaConfig",
    "OWNER_QUERY_PROMPT",
    "RAG_PROMPT",
    "SYSTEM_PROMPT",
]
