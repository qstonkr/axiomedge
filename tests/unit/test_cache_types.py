"""Unit tests for cache types — DOMAIN_THRESHOLDS, CacheEntry."""

from src.cache.cache_types import (
    CacheDomain,
    CacheEntry,
    DOMAIN_THRESHOLDS,
)


class TestDomainThresholds:
    def test_all_domains_present(self) -> None:
        assert CacheDomain.POLICY in DOMAIN_THRESHOLDS
        assert CacheDomain.CODE in DOMAIN_THRESHOLDS
        assert CacheDomain.KB_SEARCH in DOMAIN_THRESHOLDS
        assert CacheDomain.GENERAL in DOMAIN_THRESHOLDS

    def test_values_match_config_weights(self) -> None:
        from src.config.weights import weights
        c = weights.cache
        assert DOMAIN_THRESHOLDS[CacheDomain.POLICY] == c.threshold_policy
        assert DOMAIN_THRESHOLDS[CacheDomain.CODE] == c.threshold_code
        assert DOMAIN_THRESHOLDS[CacheDomain.KB_SEARCH] == c.threshold_kb
        assert DOMAIN_THRESHOLDS[CacheDomain.GENERAL] == c.threshold_general

    def test_policy_is_strictest(self) -> None:
        assert DOMAIN_THRESHOLDS[CacheDomain.POLICY] >= DOMAIN_THRESHOLDS[CacheDomain.CODE]
        assert DOMAIN_THRESHOLDS[CacheDomain.CODE] >= DOMAIN_THRESHOLDS[CacheDomain.GENERAL]


class TestCacheEntry:
    def test_create_entry(self) -> None:
        entry = CacheEntry(
            key="test_key",
            query="test query",
            response={"answer": "test"},
        )
        assert entry.key == "test_key"
        assert entry.query == "test query"
        assert entry.response == {"answer": "test"}
