"""LLM provider registry.

Registry + factory 패턴으로 LLMClient 구현체를 plug-in 처럼 등록한다.
호출자는 ``create_llm_client(name)`` 만 호출하면 되고, 새 provider 추가
시 decorator 한 줄로 등록 가능.

### 등록 예시

```python
from src.core.providers.llm import register_llm_provider
from src.llm.types import LLMClient

@register_llm_provider("claude")
def create_claude(settings) -> LLMClient:
    from my_pkg.claude import ClaudeClient
    return ClaudeClient(api_key=settings.anthropic.api_key, ...)
```

### 선택 우선순위

1. 명시적 호출: ``create_llm_client("ollama")``
2. 기본값: env var ``LLM_PROVIDER`` (default: "ollama")
3. Legacy: ``USE_SAGEMAKER_LLM=true`` 도 여전히 "sagemaker" 로 매핑 (backward compat)

### 내장 provider

- ``ollama`` (기본) — `src/llm/ollama_client.py::OllamaClient`
- ``sagemaker`` — `src/llm/sagemaker_client.py::SageMakerLLMClient`

새 provider 를 추가하려면 이 파일 또는 별도 모듈에서 ``@register_llm_provider``
로 등록하고, 등록된 모듈이 앱 초기화 시 import 되도록 한다.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.config import Settings
    from src.llm.types import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Factory: Settings → LLMClient
LLMProviderFactory = Callable[["Settings"], "LLMClient"]

LLM_PROVIDER_REGISTRY: dict[str, LLMProviderFactory] = {}


def register_llm_provider(name: str) -> Callable[[LLMProviderFactory], LLMProviderFactory]:
    """LLM provider factory 를 registry 에 등록하는 decorator."""
    def decorator(factory: LLMProviderFactory) -> LLMProviderFactory:
        if name in LLM_PROVIDER_REGISTRY:
            logger.warning(
                "LLM provider %r already registered — overwriting (old=%s, new=%s)",
                name, LLM_PROVIDER_REGISTRY[name].__qualname__, factory.__qualname__,
            )
        LLM_PROVIDER_REGISTRY[name] = factory
        return factory
    return decorator


def create_llm_client(
    provider_name: str | None = None,
    settings: "Settings | None" = None,
) -> "LLMClient":
    """Registry 에서 LLM client 인스턴스 생성.

    Args:
        provider_name: 이름 명시. None 이면 env 해석.
        settings: 주입. None 이면 ``get_settings()`` 사용.

    Returns:
        LLMClient 인스턴스 (Protocol 만족).

    Raises:
        ValueError: 등록되지 않은 provider 이름.
    """
    if settings is None:
        from src.config import get_settings
        settings = get_settings()

    resolved = _resolve_provider_name(provider_name)
    factory = LLM_PROVIDER_REGISTRY.get(resolved)
    if factory is None:
        available = sorted(LLM_PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"Unknown LLM provider: {resolved!r}. Registered: {available}. "
            "Use @register_llm_provider to add a new one.",
        )

    client = factory(settings)
    logger.info("LLM provider initialized: %s (%s)", resolved, type(client).__name__)
    return client


def _resolve_provider_name(explicit: str | None) -> str:
    """선택 우선순위에 따라 provider 이름 결정."""
    if explicit:
        return explicit

    # Legacy env var — 역시 지원 (backward compat).
    if os.getenv("USE_SAGEMAKER_LLM", "false").lower() == "true":
        return "sagemaker"

    return os.getenv("LLM_PROVIDER", "ollama")


# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------

@register_llm_provider("ollama")
def _create_ollama(settings: "Settings") -> "LLMClient":
    from src.llm.ollama_client import OllamaClient, OllamaConfig
    config = OllamaConfig(
        base_url=settings.ollama.base_url,
        model=settings.ollama.model,
        context_length=settings.ollama.context_length,
    )
    return OllamaClient(config=config)


@register_llm_provider("sagemaker")
def _create_sagemaker(settings: "Settings") -> "LLMClient":  # noqa: ARG001 (settings 미사용 — SageMakerConfig 가 env 로드)
    from src.llm.sagemaker_client import SageMakerConfig, SageMakerLLMClient
    return SageMakerLLMClient(config=SageMakerConfig())
