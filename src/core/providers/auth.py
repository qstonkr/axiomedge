"""Auth provider registry.

`src/auth/providers.py` 의 if-elif 체인 (local/keycloak/azure_ad/internal)
과 `src/api/app.py::_init_auth` 의 kwargs 조립 분기를 중앙화한 registry.

호출자는 ``create_auth_provider(name, settings, state)`` 만 부르고, 각
provider 는 decorator 로 등록된 setup 함수에서 Settings 에서 필요한 값을
직접 뽑아 `AuthProviderBase` 를 생성한다. 이렇게 하면:

- 새 provider 추가 시 이 모듈에 @register 함수 하나만 추가
- `app.py` 의 if-elif 체인 제거 (단순 create_auth_provider 호출로 축소)
- `src/auth/providers.py::create_auth_provider` 는 facade 로 re-export

### 등록된 provider

- ``local`` — `LocalAuthProvider` (기본. 개발용 API key)
- ``internal`` — `InternalAuthProvider` + JWT service (self-issued token)
- ``keycloak`` — `KeycloakAuthProvider`
- ``azure_ad`` — `AzureADAuthProvider`

새 provider 를 추가하려면 이 파일에 `@register_auth_provider("name")`
데코레이터와 함께 setup 함수를 정의.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.auth.providers import AuthProviderBase
    from src.config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Factory: (Settings, state dict) → AuthProviderBase
# state 를 주는 이유 — internal provider 는 jwt_service 를 state 에 저장해야
# 나중에 route 에서 접근 가능. 다른 provider 는 state 를 건드릴 필요 없음.
AuthProviderFactory = Callable[["Settings", dict[str, Any]], "AuthProviderBase"]

AUTH_PROVIDER_REGISTRY: dict[str, AuthProviderFactory] = {}


def register_auth_provider(name: str) -> Callable[[AuthProviderFactory], AuthProviderFactory]:
    """Auth provider factory 등록 decorator."""
    def decorator(factory: AuthProviderFactory) -> AuthProviderFactory:
        if name in AUTH_PROVIDER_REGISTRY:
            logger.warning(
                "Auth provider %r already registered — overwriting (old=%s, new=%s)",
                name, AUTH_PROVIDER_REGISTRY[name].__qualname__, factory.__qualname__,
            )
        AUTH_PROVIDER_REGISTRY[name] = factory
        return factory
    return decorator


def create_auth_provider(
    provider_name: str,
    settings: "Settings",
    state: dict[str, Any] | None = None,
) -> "AuthProviderBase":
    """Registry 에서 auth provider 인스턴스 생성.

    Args:
        provider_name: "local" / "internal" / "keycloak" / "azure_ad"
        settings: 전체 Settings 객체 (sub-config 액세스)
        state: app state dict. 일부 provider (internal) 가 jwt_service 등을 저장.

    Returns:
        `AuthProviderBase` 인스턴스.

    Raises:
        ValueError: 등록되지 않은 provider 이름이거나 필수 설정 누락.
    """
    if state is None:
        state = {}

    factory = AUTH_PROVIDER_REGISTRY.get(provider_name)
    if factory is None:
        available = sorted(AUTH_PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"Unknown auth provider: {provider_name!r}. Registered: {available}. "
            "Use @register_auth_provider to add a new one.",
        )

    provider = factory(settings, state)
    logger.info("Auth provider initialized: %s (%s)", provider_name, type(provider).__name__)
    return provider


# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------

@register_auth_provider("local")
def _create_local(settings: "Settings", state: dict[str, Any]) -> "AuthProviderBase":  # noqa: ARG001
    from src.auth.providers import LocalAuthProvider
    try:
        api_keys = json.loads(settings.auth.local_api_keys)
    except json.JSONDecodeError as e:
        logger.warning("Invalid AUTH_LOCAL_API_KEYS JSON — using empty dict: %s", e)
        api_keys = {}
    return LocalAuthProvider(api_keys=api_keys)


@register_auth_provider("internal")
def _create_internal(settings: "Settings", state: dict[str, Any]) -> "AuthProviderBase":
    from src.auth.jwt_service import JWTService
    from src.auth.providers import InternalAuthProvider

    auth = settings.auth
    if not auth.jwt_secret:
        raise ValueError(
            "AUTH_JWT_SECRET is required when AUTH_PROVIDER=internal. "
            "Generate one with: openssl rand -hex 32",
        )
    jwt_svc = JWTService(
        secret_key=auth.jwt_secret,
        algorithm=auth.jwt_algorithm,
        access_token_expire_minutes=auth.jwt_access_expire_minutes,
        refresh_token_expire_hours=auth.jwt_refresh_expire_hours,
        issuer=auth.jwt_issuer,
    )
    state["jwt_service"] = jwt_svc
    return InternalAuthProvider(jwt_service=jwt_svc)


@register_auth_provider("keycloak")
def _create_keycloak(settings: "Settings", state: dict[str, Any]) -> "AuthProviderBase":  # noqa: ARG001
    from src.auth.providers import KeycloakAuthProvider
    auth = settings.auth
    return KeycloakAuthProvider(
        server_url=auth.keycloak_url,
        realm=auth.keycloak_realm,
        client_id=auth.keycloak_client_id,
        client_secret=auth.keycloak_client_secret,
    )


@register_auth_provider("azure_ad")
def _create_azure_ad(settings: "Settings", state: dict[str, Any]) -> "AuthProviderBase":  # noqa: ARG001
    from src.auth.providers import AzureADAuthProvider
    auth = settings.auth
    return AzureADAuthProvider(
        tenant_id=auth.azure_ad_tenant_id,
        client_id=auth.azure_ad_client_id,
    )
