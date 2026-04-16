"""Tests for connector plugin registry."""

import pytest

from src.providers.connector import (
    CONNECTOR_REGISTRY,
    IConnector,
    create_connector,
    list_connectors,
    register_connector,
)


class TestConnectorRegistry:
    def setup_method(self):
        self._backup = dict(CONNECTOR_REGISTRY)
        CONNECTOR_REGISTRY.clear()

    def teardown_method(self):
        CONNECTOR_REGISTRY.clear()
        CONNECTOR_REGISTRY.update(self._backup)

    def test_register_and_create(self):
        @register_connector("test")
        class TestConnector:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def crawl(self, config):
                return []

        instance = create_connector("test", url="http://example.com")
        assert instance.kwargs["url"] == "http://example.com"

    def test_unknown_connector_raises(self):
        with pytest.raises(ValueError, match="Unknown connector"):
            create_connector("nonexistent")

    def test_list_connectors(self):
        @register_connector("alpha")
        class A:
            pass

        @register_connector("beta")
        class B:
            pass

        assert list_connectors() == ["alpha", "beta"]

    def test_overwrite_warning(self):
        @register_connector("dup")
        class First:
            pass

        @register_connector("dup")
        class Second:
            pass

        assert CONNECTOR_REGISTRY["dup"] is Second

    def test_protocol_check(self):
        class ValidConnector:
            async def crawl(self, config):
                return []

        assert isinstance(ValidConnector(), IConnector)
