"""Unit tests for LLMClient Protocol compliance."""

from src.nlp.llm.types import LLMClient
from src.nlp.llm.ollama_client import OllamaClient


class TestLLMClientProtocol:
    def test_ollama_satisfies_protocol(self) -> None:
        client = OllamaClient()
        assert isinstance(client, LLMClient)

    def test_arbitrary_class_does_not_satisfy(self) -> None:
        class NotLLM:
            pass
        assert not isinstance(NotLLM(), LLMClient)
