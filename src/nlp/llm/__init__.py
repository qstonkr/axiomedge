"""LLM clients for local knowledge generation."""

from .ollama_client import OllamaClient, OllamaConfig
from .prompts import OWNER_QUERY_PROMPT, RAG_PROMPT, SYSTEM_PROMPT
from .sagemaker_client import SageMakerConfig, SageMakerLLMClient

__all__ = [
    "OllamaClient",
    "OllamaConfig",
    "SageMakerConfig",
    "SageMakerLLMClient",
    "OWNER_QUERY_PROMPT",
    "RAG_PROMPT",
    "SYSTEM_PROMPT",
]
