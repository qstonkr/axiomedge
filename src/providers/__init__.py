"""Provider registries — plugin-style factories for swappable backends.

각 provider 유형 (LLM, embedding, auth 등) 에 대해 **registry + factory**
패턴을 제공한다. 새 provider 추가 시 decorator 한 줄로 등록하면 되고,
호출자는 ``create_*_provider(name)`` 한 함수만 알면 된다.

이전에는 `src/api/app.py` 의 초기화 경로에 if-elif 체인이 박혀 있어 새
provider 추가 시 3~5 파일 수정이 필요했다. Registry 패턴으로 1~2 파일만
건드리면 되도록 단순화.

구조:
    src/providers/
    ├── __init__.py   — 이 파일. facade re-export
    ├── llm.py        — LLM registry + factory
    ├── auth.py       — Auth registry + factory

기존 `src/embedding/provider_factory.py` 는 이미 factory 역할을 하고 있어
중복 생성 금지. 필요 시 Phase C 에서 이 디렉터리로 이동.
"""

from __future__ import annotations

from src.providers.auth import (
    AUTH_PROVIDER_REGISTRY,
    create_auth_provider,
    register_auth_provider,
)
from src.providers.llm import (
    LLM_PROVIDER_REGISTRY,
    create_llm_client,
    register_llm_provider,
)

__all__ = [
    "AUTH_PROVIDER_REGISTRY",
    "LLM_PROVIDER_REGISTRY",
    "create_auth_provider",
    "create_llm_client",
    "register_auth_provider",
    "register_llm_provider",
]
