"""Bloom Filter for Stage 1 Pre-filter.

Probabilistic data structure for membership testing.
False positives possible, false negatives impossible.

Features:
- O(1) lookup/insert
- Memory efficient
- Double hashing technique

Usage:
- URL hash duplicate check
- Title hash duplicate check
- Content hash duplicate check
- 30-40% filtering effect

Adapted from oreo-ecosystem infrastructure/dedup/bloom_filter.py.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any


class BloomFilter:
    """Bloom Filter.

    Probabilistic data structure for membership testing.
    False positives possible, false negatives impossible.

    Parameters:
        expected_items: Expected number of items
        fp_rate: False Positive rate (default: 0.01 = 1%)
    """

    def __init__(self, expected_items: int = 100_000, fp_rate: float = 0.01) -> None:
        self._expected_items = expected_items
        self._fp_rate = fp_rate

        # Optimal bit array size: m = -n*ln(p) / (ln(2)^2)
        self._size = int(-expected_items * math.log(fp_rate) / (math.log(2) ** 2))

        # Optimal number of hash functions: k = (m/n) * ln(2)
        self._num_hashes = int((self._size / expected_items) * math.log(2))
        self._num_hashes = max(1, self._num_hashes)

        # Bit array
        self._bit_array = [False] * self._size

        self._count = 0

    def _get_hash_positions(self, item: str) -> list[int]:
        """Calculate hash positions for an item.

        Uses double hashing: h(i) = (h1 + i * h2) % m
        """
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha256(item.encode()).hexdigest(), 16)

        positions = []
        for i in range(self._num_hashes):
            pos = (h1 + i * h2) % self._size
            positions.append(pos)

        return positions

    def add(self, item: str) -> None:
        """Add an item to the filter."""
        positions = self._get_hash_positions(item)
        for pos in positions:
            self._bit_array[pos] = True
        self._count += 1

    def contains(self, item: str) -> bool:
        """Check if an item might exist (false positives possible)."""
        positions = self._get_hash_positions(item)
        return all(self._bit_array[pos] for pos in positions)

    def __contains__(self, item: str) -> bool:
        """Support 'in' operator."""
        return self.contains(item)

    @property
    def count(self) -> int:
        """Number of items added."""
        return self._count

    @property
    def size(self) -> int:
        """Bit array size."""
        return self._size

    @property
    def false_positive_rate(self) -> float:
        """Current estimated false positive rate: (1 - e^(-k*n/m))^k"""
        k = self._num_hashes
        n = self._count
        m = self._size

        if n == 0:
            return 0.0

        return (1 - math.exp(-k * n / m)) ** k

    def clear(self) -> None:
        """Reset the filter."""
        self._bit_array = [False] * self._size
        self._count = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize state."""
        return {
            "expected_items": self._expected_items,
            "fp_rate": self._fp_rate,
            "size": self._size,
            "num_hashes": self._num_hashes,
            "count": self._count,
            "current_fp_rate": self.false_positive_rate,
        }


class ScalableBloomFilter:
    """Scalable Bloom Filter.

    Auto-expanding Bloom Filter. Adds new filters when capacity is reached.
    """

    def __init__(
        self,
        initial_capacity: int = 100_000,
        fp_rate: float = 0.01,
        growth_factor: float = 2.0,
    ) -> None:
        self._initial_capacity = initial_capacity
        self._fp_rate = fp_rate
        self._growth_factor = growth_factor

        self._filters: list[BloomFilter] = [
            BloomFilter(initial_capacity, fp_rate)
        ]

    def add(self, item: str) -> None:
        """Add an item."""
        current_filter = self._filters[-1]

        # Add new filter when current is full
        current_capacity = int(
            self._initial_capacity * (self._growth_factor ** (len(self._filters) - 1))
        )
        if current_filter.count >= current_capacity:
            new_capacity = int(
                self._initial_capacity * (self._growth_factor ** len(self._filters))
            )
            new_filter = BloomFilter(new_capacity, self._fp_rate / 2)
            self._filters.append(new_filter)
            current_filter = new_filter

        current_filter.add(item)

    def contains(self, item: str) -> bool:
        """Check if item might exist."""
        return any(f.contains(item) for f in self._filters)

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    @property
    def count(self) -> int:
        """Total items across all filters."""
        return sum(f.count for f in self._filters)

    def clear(self) -> None:
        """Reset all filters."""
        self._filters = [BloomFilter(self._initial_capacity, self._fp_rate)]
