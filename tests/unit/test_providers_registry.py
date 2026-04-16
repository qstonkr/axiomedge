"""Tests for src/providers/ — LLM + Auth registry (PR8)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# LLM provider registry
# ---------------------------------------------------------------------------


class TestLLMRegistry:
    def _clear_test_entries(self, monkeypatch):
        """Test-local provider 는 테스트 종료 후 정리."""
        from src.providers.llm import LLM_PROVIDER_REGISTRY
        snapshot = dict(LLM_PROVIDER_REGISTRY)
        monkeypatch.setattr(
            "src.providers.llm.LLM_PROVIDER_REGISTRY",
            dict(snapshot),
        )

    def test_builtin_ollama_registered(self):
        from src.providers.llm import LLM_PROVIDER_REGISTRY
        assert "ollama" in LLM_PROVIDER_REGISTRY

    def test_builtin_sagemaker_registered(self):
        from src.providers.llm import LLM_PROVIDER_REGISTRY
        assert "sagemaker" in LLM_PROVIDER_REGISTRY

    def test_register_decorator_adds_entry(self, monkeypatch):
        self._clear_test_entries(monkeypatch)
        from src.providers.llm import LLM_PROVIDER_REGISTRY, register_llm_provider

        @register_llm_provider("test_dummy")
        def _factory(settings):
            return MagicMock()

        assert LLM_PROVIDER_REGISTRY["test_dummy"] is _factory

    def test_unknown_provider_raises(self, monkeypatch):
        from src.providers.llm import create_llm_client

        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("USE_SAGEMAKER_LLM", raising=False)

        with pytest.raises(ValueError, match="Unknown LLM provider"):
            create_llm_client("not_a_real_provider", settings=MagicMock())

    def test_resolve_explicit_name_wins(self, monkeypatch):
        from src.providers.llm import _resolve_provider_name
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("USE_SAGEMAKER_LLM", "true")
        # explicit 이 우선
        assert _resolve_provider_name("custom") == "custom"

    def test_resolve_legacy_use_sagemaker(self, monkeypatch):
        from src.providers.llm import _resolve_provider_name
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.setenv("USE_SAGEMAKER_LLM", "true")
        assert _resolve_provider_name(None) == "sagemaker"

    def test_resolve_env_var(self, monkeypatch):
        from src.providers.llm import _resolve_provider_name
        monkeypatch.setenv("LLM_PROVIDER", "my_provider")
        monkeypatch.delenv("USE_SAGEMAKER_LLM", raising=False)
        assert _resolve_provider_name(None) == "my_provider"

    def test_resolve_default_ollama(self, monkeypatch):
        from src.providers.llm import _resolve_provider_name
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("USE_SAGEMAKER_LLM", raising=False)
        assert _resolve_provider_name(None) == "ollama"

    def test_create_llm_client_calls_registered_factory(self, monkeypatch):
        from src.providers.llm import LLM_PROVIDER_REGISTRY, create_llm_client

        fake_client = MagicMock(name="FakeLLMClient")
        fake_factory = MagicMock(return_value=fake_client)

        # Replace registry temporarily
        monkeypatch.setitem(LLM_PROVIDER_REGISTRY, "test_llm", fake_factory)

        settings = MagicMock()
        result = create_llm_client("test_llm", settings=settings)

        fake_factory.assert_called_once_with(settings)
        assert result is fake_client


# ---------------------------------------------------------------------------
# Auth provider registry
# ---------------------------------------------------------------------------


class TestAuthRegistry:
    def test_builtin_providers_registered(self):
        from src.providers.auth import AUTH_PROVIDER_REGISTRY
        for name in ("local", "internal", "keycloak", "azure_ad"):
            assert name in AUTH_PROVIDER_REGISTRY, f"Missing: {name}"

    def test_unknown_provider_raises(self):
        from src.providers.auth import create_auth_provider
        with pytest.raises(ValueError, match="Unknown auth provider"):
            create_auth_provider("not_real", MagicMock())

    def test_local_provider_handles_bad_json(self):
        from src.providers.auth import create_auth_provider
        settings = MagicMock()
        settings.auth.local_api_keys = "{not json}"
        provider = create_auth_provider("local", settings)
        # LocalAuthProvider 가 생성됐는지만 확인
        assert provider is not None
        assert type(provider).__name__ == "LocalAuthProvider"

    def test_local_provider_parses_valid_json(self):
        from src.providers.auth import create_auth_provider
        settings = MagicMock()
        settings.auth.local_api_keys = '{"key1": {"email": "a@b.c", "roles": []}}'
        provider = create_auth_provider("local", settings)
        assert type(provider).__name__ == "LocalAuthProvider"

    def test_internal_requires_jwt_secret(self):
        from src.providers.auth import create_auth_provider
        settings = MagicMock()
        settings.auth.jwt_secret = ""  # missing
        with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
            create_auth_provider("internal", settings, state={})

    def test_internal_stores_jwt_service_in_state(self):
        from src.providers.auth import create_auth_provider
        settings = MagicMock()
        settings.auth.jwt_secret = "test-secret-abc"
        settings.auth.jwt_algorithm = "HS256"
        settings.auth.jwt_access_expire_minutes = 60
        settings.auth.jwt_refresh_expire_hours = 8
        settings.auth.jwt_issuer = "test"
        state: dict = {}
        provider = create_auth_provider("internal", settings, state)
        assert type(provider).__name__ == "InternalAuthProvider"
        assert "jwt_service" in state
        assert state["jwt_service"] is not None

    def test_register_decorator(self, monkeypatch):
        from src.providers.auth import (
            AUTH_PROVIDER_REGISTRY,
            create_auth_provider,
            register_auth_provider,
        )

        fake_provider = MagicMock(name="FakeAuthProvider")

        @register_auth_provider("test_auth")
        def _factory(settings, state):
            return fake_provider

        try:
            result = create_auth_provider("test_auth", MagicMock(), state={})
            assert result is fake_provider
            assert "test_auth" in AUTH_PROVIDER_REGISTRY
        finally:
            AUTH_PROVIDER_REGISTRY.pop("test_auth", None)

    def test_state_optional(self):
        """state 생략 시 빈 dict 로 동작."""
        from src.providers.auth import create_auth_provider
        settings = MagicMock()
        settings.auth.local_api_keys = "{}"
        provider = create_auth_provider("local", settings)  # state 없이
        assert provider is not None


# ---------------------------------------------------------------------------
# Package-level re-exports
# ---------------------------------------------------------------------------


class TestPackageInit:
    def test_providers_exports(self):
        from src import providers
        assert hasattr(providers, "create_llm_client")
        assert hasattr(providers, "create_auth_provider")
        assert hasattr(providers, "register_llm_provider")
        assert hasattr(providers, "register_auth_provider")
        assert hasattr(providers, "LLM_PROVIDER_REGISTRY")
        assert hasattr(providers, "AUTH_PROVIDER_REGISTRY")
