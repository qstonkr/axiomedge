"""Unit tests for BloomFilter and ScalableBloomFilter."""

from __future__ import annotations

import math

import pytest

from src.pipelines.dedup.bloom_filter import BloomFilter, ScalableBloomFilter


class TestBloomFilter:
    """Tests for BloomFilter."""

    def test_add_and_contains(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("hello")
        assert bf.contains("hello") is True

    def test_not_contains_absent_item(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("hello")
        assert bf.contains("world") is False

    def test_in_operator(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("test")
        assert "test" in bf
        assert "missing" not in bf

    def test_count(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        assert bf.count == 0
        bf.add("a")
        bf.add("b")
        bf.add("c")
        assert bf.count == 3

    def test_size_is_positive(self):
        bf = BloomFilter(expected_items=1000, fp_rate=0.01)
        assert bf.size > 0

    def test_optimal_size_formula(self):
        n, p = 1000, 0.01
        bf = BloomFilter(expected_items=n, fp_rate=p)
        expected_size = int(-n * math.log(p) / (math.log(2) ** 2))
        assert bf.size == expected_size

    def test_false_positive_rate_zero_when_empty(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        assert bf.false_positive_rate == 0.0

    def test_false_positive_rate_increases_with_items(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("item1")
        rate1 = bf.false_positive_rate
        for i in range(50):
            bf.add(f"item_{i}")
        rate2 = bf.false_positive_rate
        assert rate2 > rate1

    def test_false_positive_rate_stays_reasonable(self):
        """When items added == expected_items, FP rate should be near configured rate."""
        n = 1000
        target_fp = 0.01
        bf = BloomFilter(expected_items=n, fp_rate=target_fp)
        for i in range(n):
            bf.add(f"item_{i}")
        assert bf.false_positive_rate < 0.05

    def test_empirical_false_positive_rate(self):
        """Measure actual false positive rate over many lookups."""
        n = 1000
        bf = BloomFilter(expected_items=n, fp_rate=0.01)
        for i in range(n):
            bf.add(f"added_{i}")

        fp_count = 0
        test_count = 5000
        for i in range(test_count):
            if bf.contains(f"not_added_{i}"):
                fp_count += 1

        actual_rate = fp_count / test_count
        assert actual_rate < 0.05, f"FP rate too high: {actual_rate:.4f}"

    def test_no_false_negatives(self):
        """Items added must always be found."""
        bf = BloomFilter(expected_items=500, fp_rate=0.01)
        items = [f"item_{i}" for i in range(500)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.contains(item) is True

    def test_clear(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("a")
        bf.add("b")
        bf.clear()
        assert bf.count == 0
        assert bf.contains("a") is False

    def test_to_dict(self):
        bf = BloomFilter(expected_items=100, fp_rate=0.01)
        bf.add("x")
        d = bf.to_dict()
        assert d["expected_items"] == 100
        assert d["fp_rate"] == 0.01
        assert d["count"] == 1
        assert "size" in d
        assert "num_hashes" in d
        assert "current_fp_rate" in d

    def test_different_fp_rates_affect_size(self):
        bf_low = BloomFilter(expected_items=1000, fp_rate=0.001)
        bf_high = BloomFilter(expected_items=1000, fp_rate=0.1)
        assert bf_low.size > bf_high.size

    def test_num_hashes_at_least_one(self):
        bf = BloomFilter(expected_items=1, fp_rate=0.99)
        assert bf._num_hashes >= 1


class TestScalableBloomFilter:
    """Tests for ScalableBloomFilter."""

    def test_add_and_contains(self):
        sbf = ScalableBloomFilter(initial_capacity=100, fp_rate=0.01)
        sbf.add("hello")
        assert sbf.contains("hello") is True
        assert sbf.contains("missing") is False

    def test_in_operator(self):
        sbf = ScalableBloomFilter(initial_capacity=100, fp_rate=0.01)
        sbf.add("x")
        assert "x" in sbf
        assert "y" not in sbf

    def test_count(self):
        sbf = ScalableBloomFilter(initial_capacity=100, fp_rate=0.01)
        for i in range(10):
            sbf.add(f"item_{i}")
        assert sbf.count == 10

    def test_auto_expand_on_capacity(self):
        """When initial capacity is exceeded, new filter should be added."""
        sbf = ScalableBloomFilter(initial_capacity=10, fp_rate=0.01)
        for i in range(20):
            sbf.add(f"item_{i}")
        assert len(sbf._filters) > 1

    def test_contains_after_expansion(self):
        """Items added before expansion should still be found."""
        sbf = ScalableBloomFilter(initial_capacity=10, fp_rate=0.01)
        items = [f"item_{i}" for i in range(30)]
        for item in items:
            sbf.add(item)
        for item in items:
            assert sbf.contains(item) is True

    def test_clear(self):
        sbf = ScalableBloomFilter(initial_capacity=10, fp_rate=0.01)
        for i in range(20):
            sbf.add(f"item_{i}")
        sbf.clear()
        assert sbf.count == 0
        assert len(sbf._filters) == 1
        assert sbf.contains("item_0") is False

    def test_growth_factor(self):
        """With growth_factor=2, second filter capacity should be double."""
        sbf = ScalableBloomFilter(initial_capacity=5, fp_rate=0.01, growth_factor=2.0)
        for i in range(6):
            sbf.add(f"item_{i}")
        assert len(sbf._filters) == 2
        assert sbf._filters[1].size > sbf._filters[0].size
