"""SageMaker AgentLLM — AWS-hosted EXAONE 등.

기존 ``SageMakerLLMClient`` 를 wrap.
"""

from __future__ import annotations

from src.agentic.llm.base import JsonAgentLLM
from src.nlp.llm.types import LLMClient


class SageMakerAgentLLM(JsonAgentLLM):
    def __init__(self, client: LLMClient | None = None) -> None:
        if client is None:
            from src.nlp.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
            client = SageMakerLLMClient(config=SageMakerConfig())
        super().__init__(client=client, provider_name="sagemaker")
