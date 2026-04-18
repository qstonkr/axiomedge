"""Pluggable Auth Providers - Keycloak, AzureAD, Local.

Each provider implements token verification and user info extraction.
Switch provider via AUTH_PROVIDER env var (default: "local" for development).
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AuthUser:
    """Normalized user identity from any auth provider."""

    sub: str  # Unique subject identifier
    email: str
    display_name: str
    provider: str  # keycloak | azure_ad | local
    roles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    department: str | None = None
    # Default/home organization per IdP claim. May be different from active_org_id
    # when a user belongs to multiple orgs (consultants, switching contexts).
    organization_id: str | None = None
    # Currently selected organization for this token's session — set by JWT claim.
    # All multi-tenant scoping should use this, not organization_id.
    active_org_id: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)


class AuthProviderBase(abc.ABC):
    """Abstract auth provider interface."""

    @abc.abstractmethod
    async def verify_token(self, token: str) -> AuthUser:
        """Verify JWT/token and return normalized user.

        Raises:
            AuthenticationError: Invalid or expired token.
        """

    @abc.abstractmethod
    async def get_jwks_uri(self) -> str | None:
        """Return JWKS URI for public key discovery (None for local)."""

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Provider identifier."""


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    def __init__(self, detail: str = "Authentication failed", status_code: int = 401) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


# =============================================================================
# Local Provider (development / testing)
# =============================================================================


class LocalAuthProvider(AuthProviderBase):
    """Local auth provider for development.

    Accepts API key or generates anonymous user.
    In production, replace with Keycloak or AzureAD.
    """

    def __init__(self, api_keys: dict[str, dict] | None = None) -> None:
        # api_keys: {"key123": {"email": "...", "name": "...", "roles": [...]}}
        self._api_keys = api_keys or {}

    @property
    def provider_name(self) -> str:
        return "local"

    async def get_jwks_uri(self) -> str | None:
        return None

    async def verify_token(self, token: str) -> AuthUser:
        if not token:
            raise AuthenticationError("No token provided")

        # Check static API keys
        if token in self._api_keys:
            info = self._api_keys[token]
            return AuthUser(
                sub=f"local:{info.get('email', 'unknown')}",
                email=info.get("email", "unknown@local"),
                display_name=info.get("name", "Local User"),
                provider="local",
                roles=info.get("roles", ["viewer"]),
                department=info.get("department"),
                organization_id=info.get("organization_id"),
            )

        raise AuthenticationError("Invalid API key")


# =============================================================================
# Keycloak Provider
# =============================================================================


class KeycloakAuthProvider(AuthProviderBase):
    """Keycloak OIDC auth provider.

    Configuration:
        KEYCLOAK_URL: https://keycloak.example.com
        KEYCLOAK_REALM: knowledge
        KEYCLOAK_CLIENT_ID: knowledge-local
    """

    def __init__(
        self,
        server_url: str,
        realm: str,
        client_id: str,
        client_secret: str | None = None,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._jwks_client: Any = None

    @property
    def provider_name(self) -> str:
        return "keycloak"

    async def get_jwks_uri(self) -> str:
        return f"{self._server_url}/realms/{self._realm}/protocol/openid-connect/certs"

    async def verify_token(self, token: str) -> AuthUser:
        import jwt as pyjwt  # PyJWT

        if self._jwks_client is None:
            jwks_uri = await self.get_jwks_uri()
            self._jwks_client = pyjwt.PyJWKClient(jwks_uri)

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"verify_exp": True},
            )
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("Token expired")
        except pyjwt.InvalidTokenError as e:
            raise AuthenticationError(f"Invalid token: {e}")

        # Extract Keycloak-specific claims
        realm_access = claims.get("realm_access", {})
        roles = realm_access.get("roles", [])

        # Resource-level roles
        resource_access = claims.get("resource_access", {})
        client_roles = resource_access.get(self._client_id, {}).get("roles", [])
        roles.extend(client_roles)

        return AuthUser(
            sub=claims["sub"],
            email=claims.get("email", ""),
            display_name=claims.get("preferred_username", claims.get("name", "")),
            provider="keycloak",
            roles=roles,
            groups=claims.get("groups", []),
            department=claims.get("department"),
            organization_id=claims.get("organization_id"),
            raw_claims=claims,
        )


# =============================================================================
# Azure AD Provider
# =============================================================================


class AzureADAuthProvider(AuthProviderBase):
    """Azure AD / Entra ID OIDC auth provider.

    Configuration:
        AZURE_AD_TENANT_ID: your-tenant-id
        AZURE_AD_CLIENT_ID: your-client-id
    """

    def __init__(self, tenant_id: str, client_id: str) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._jwks_client: Any = None

    @property
    def provider_name(self) -> str:
        return "azure_ad"

    async def get_jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self._tenant_id}/discovery/v2.0/keys"

    async def verify_token(self, token: str) -> AuthUser:
        import jwt as pyjwt

        if self._jwks_client is None:
            jwks_uri = await self.get_jwks_uri()
            self._jwks_client = pyjwt.PyJWKClient(jwks_uri)

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=f"https://login.microsoftonline.com/{self._tenant_id}/v2.0",
                options={"verify_exp": True},
            )
        except pyjwt.ExpiredSignatureError:
            raise AuthenticationError("Token expired")
        except pyjwt.InvalidTokenError as e:
            raise AuthenticationError(f"Invalid token: {e}")

        # Azure AD role/group claims
        roles = claims.get("roles", [])
        groups = claims.get("groups", [])

        return AuthUser(
            sub=claims["oid"],  # Azure AD uses 'oid' as stable user ID
            email=claims.get("preferred_username", claims.get("email", "")),
            display_name=claims.get("name", ""),
            provider="azure_ad",
            roles=roles,
            groups=groups,
            department=claims.get("department"),
            organization_id=claims.get("tid"),  # Tenant as org
            raw_claims=claims,
        )


# =============================================================================
# Internal Auth Provider (email/password login with JWT)
# =============================================================================


class InternalAuthProvider(AuthProviderBase):
    """Internal email/password auth provider with JWT tokens.

    Used when AUTH_PROVIDER="internal". Verifies JWT access tokens
    issued by the internal login endpoint.
    """

    def __init__(self, jwt_service: Any, revoked_token_store: Any | None = None) -> None:
        from src.auth.jwt_service import JWTService

        self._jwt_service: JWTService = jwt_service
        self._revoked_store = revoked_token_store

    @property
    def provider_name(self) -> str:
        return "internal"

    async def get_jwks_uri(self) -> str | None:
        return None  # HS256, no JWKS

    async def verify_token(self, token: str) -> AuthUser:
        """Verify a JWT access token and return AuthUser."""
        claims = self._jwt_service.verify_access_token(token)
        if self._revoked_store is not None:
            jti = claims.get("jti")
            if jti and await self._revoked_store.is_revoked(jti):
                raise AuthenticationError("Token revoked")
        return AuthUser(
            sub=claims["sub"],
            email=claims.get("email", ""),
            display_name=claims.get("display_name", claims.get("email", "")),
            provider="internal",
            roles=claims.get("roles", []),
            active_org_id=claims.get("active_org_id"),
            raw_claims=claims,
        )


# =============================================================================
# Provider Factory
# =============================================================================


def create_auth_provider(
    provider: str = "local",
    **kwargs: Any,
) -> AuthProviderBase:
    """Create auth provider from config.

    Args:
        provider: "local" | "keycloak" | "azure_ad" | "internal"
        **kwargs: Provider-specific configuration

    Returns:
        Configured AuthProviderBase instance.
    """
    if provider == "internal":
        jwt_service = kwargs.get("jwt_service")
        if not jwt_service:
            raise ValueError("jwt_service required for internal provider")
        return InternalAuthProvider(jwt_service=jwt_service)
    elif provider == "keycloak":
        return KeycloakAuthProvider(
            server_url=kwargs["server_url"],
            realm=kwargs["realm"],
            client_id=kwargs["client_id"],
            client_secret=kwargs.get("client_secret"),
        )
    elif provider == "azure_ad":
        return AzureADAuthProvider(
            tenant_id=kwargs["tenant_id"],
            client_id=kwargs["client_id"],
        )
    else:
        return LocalAuthProvider(api_keys=kwargs.get("api_keys", {}))
