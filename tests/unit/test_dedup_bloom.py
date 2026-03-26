"""Unit tests for Bloom Filter (Stage 1 Pre-filter)."""

from __future__ import annotations

from src.pipeline.dedup.bloom_filter import BloomFilter, ScalableBloomFilter


class TestBloomFilter:
    """Tests for BloomFilter."""

    def test_bloom_add_and_contains(self):
        """Added items should be found; non-added items should (mostly) not."""
        bf = BloomFilter(expected_items=1000, fp_rate=0.01)

        bf.add("hello")
        bf.add("world")

        assert bf.contains("hello")
        assert bf.contains("world")
        assert "hello" in bf  # __contains__ support
        assert not bf.contains("missing_item_xyz_12345")
        assert bf.count == 2

    def test_bloom_false_positive_rate(self):
        """After inserting expected_items, FP rate should stay near the configured rate."""
        n = 1000
        bf = BloomFilter(expected_items=n, fp_rate=0.01)

        for i in range(n):
            bf.add(f"item-{i}")

        # Estimated FP rate should be close to 0.01
        assert bf.false_positive_rate < 0.05  # generous upper bound

        # Empirical check: test 10000 non-added items
        false_positives = sum(
            1 for i in range(10_000) if bf.contains(f"nonexistent-{i}")
        )
        empirical_fp_rate = false_positives / 10_000
        assert empirical_fp_rate < 0.05, f"Empirical FP rate too high: {empirical_fp_rate}"

    def test_bloom_clear(self):
        """clear() should reset the filter completely."""
        bf = BloomFilter(expected_items=100)
        bf.add("a")
        bf.add("b")
        assert bf.count == 2
        assert bf.contains("a")

        bf.clear()

        assert bf.count == 0
        assert not bf.contains("a")
        assert not bf.contains("b")
        assert bf.false_positive_rate == 0.0


class TestScalableBloomFilter:
    """Tests for ScalableBloomFilter."""

    def test_scalable_bloom_auto_expand(self):
        """ScalableBloomFilter should auto-expand when capacity is reached."""
        sbf = ScalableBloomFilter(
            initial_capacity=10,
            fp_rate=0.01,
            growth_factor=2.0,
        )

        # Add more than initial capacity to trigger expansion
        for i in range(25):
            sbf.add(f"item-{i}")

        assert sbf.count == 25

        # All items should still be found
        for i in range(25):
            assert sbf.contains(f"item-{i}"), f"item-{i} not found after expansion"

        # Non-existent items should (mostly) not be found
        assert not sbf.contains("nonexistent-item-99999")

    def test_scalable_bloom_clear(self):
        """clear() resets to a single fresh filter."""
        sbf = ScalableBloomFilter(initial_capacity=5)
        for i in range(20):
            sbf.add(f"item-{i}")
        assert sbf.count == 20

        sbf.clear()
        assert sbf.count == 0
        assert not sbf.contains("item-0")
