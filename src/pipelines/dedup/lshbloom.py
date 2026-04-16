"""LSHBloom - MinHash LSH with Bloom Filter.

Stage 2: MinHash LSH-based similar document detection.

Features:
- Jaccard similarity based
- <10ms processing time
- 10-15% duplicate flagging
- Memory efficient

Algorithm:
1. Decompose document into shingles
2. Generate MinHash signature
3. Band bucketing via LSH
4. Return candidate pairs from same bucket

Adapted from oreo-ecosystem infrastructure/dedup/lshbloom.py.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class MinHashSignature:
    """MinHash signature.

    Attributes:
        doc_id: Document ID
        signature: List of MinHash values
    """

    doc_id: str
    signature: list[int]


@dataclass
class SimilarPair:
    """Similar document pair.

    Attributes:
        doc_id_1: First document ID
        doc_id_2: Second document ID
        estimated_similarity: Estimated Jaccard similarity
    """

    doc_id_1: str
    doc_id_2: str
    estimated_similarity: float


class MinHasher:
    """MinHash signature generator.

    Generates MinHash signatures that approximate Jaccard similarity.
    """

    def __init__(
        self,
        num_hashes: int = 128,
        shingle_size: int = 3,
        seed: int = 42,
    ):
        """Initialize.

        Args:
            num_hashes: Number of hash functions (accuracy-speed tradeoff)
            shingle_size: Shingle size (number of words)
            seed: Random seed (reproducibility)
        """
        self._num_hashes = num_hashes
        self._shingle_size = shingle_size

        # Hash function parameters: h(x) = (ax + b) % p
        rng = random.Random(seed)
        self._max_hash = 2**32 - 1
        self._prime = 4294967311  # Prime > 2^32

        self._a_params = [rng.randint(1, self._max_hash) for _ in range(num_hashes)]
        self._b_params = [rng.randint(0, self._max_hash) for _ in range(num_hashes)]

    def _shingle(self, text: str) -> set[str]:
        """Decompose text into shingles."""
        words = text.lower().split()

        if len(words) < self._shingle_size:
            return {text.lower()}

        shingles = set()
        for i in range(len(words) - self._shingle_size + 1):
            shingle = " ".join(words[i : i + self._shingle_size])
            shingles.add(shingle)

        return shingles

    def _hash_shingle(self, shingle: str, hash_idx: int) -> int:
        """Compute hash for a shingle.

        Args:
            shingle: Shingle string
            hash_idx: Hash function index

        Returns:
            Hash value
        """
        shingle_hash = int(hashlib.md5(shingle.encode()).hexdigest(), 16) % self._prime

        a = self._a_params[hash_idx]
        b = self._b_params[hash_idx]

        return (a * shingle_hash + b) % self._prime

    def compute_signature(self, doc_id: str, text: str) -> MinHashSignature:
        """Compute MinHash signature for a document."""
        shingles = self._shingle(text)

        if not shingles:
            return MinHashSignature(
                doc_id=doc_id, signature=[self._max_hash] * self._num_hashes
            )

        signature = []

        for hash_idx in range(self._num_hashes):
            min_hash = min(self._hash_shingle(s, hash_idx) for s in shingles)
            signature.append(min_hash)

        return MinHashSignature(doc_id=doc_id, signature=signature)

    @staticmethod
    def estimate_similarity(sig1: MinHashSignature, sig2: MinHashSignature) -> float:
        """Estimate Jaccard similarity between two signatures."""
        if len(sig1.signature) != len(sig2.signature):
            return 0.0

        matches = sum(1 for a, b in zip(sig1.signature, sig2.signature) if a == b)
        return matches / len(sig1.signature)


class LSHBloom:
    """LSH with Bloom Filter.

    Uses Locality-Sensitive Hashing for efficient similar document detection.

    Parameters:
    - bands: Number of bands (more bands = only higher similarity detected)
    - rows_per_band: Rows per band

    Similarity threshold:
    - Approximately documents with similarity >= (1/b)^(1/r) are detected
    - b=16, r=8: ~0.55 threshold
    """

    def __init__(
        self,
        num_hashes: int = 128,
        bands: int = 16,
        shingle_size: int = 3,
        seed: int = 42,
    ):
        self._num_hashes = num_hashes
        self._bands = bands
        self._rows_per_band = num_hashes // bands
        self._shingle_size = shingle_size

        self._min_hasher = MinHasher(num_hashes, shingle_size, seed)

        # Buckets per band: band_idx -> bucket_hash -> doc_ids
        self._buckets: list[dict[int, list[str]]] = [
            {} for _ in range(bands)
        ]

        # Signature storage
        self._signatures: dict[str, MinHashSignature] = {}

    def add(self, doc_id: str, text: str) -> None:
        """Add a document.

        Args:
            doc_id: Document ID
            text: Document text
        """
        signature = self._min_hasher.compute_signature(doc_id, text)
        self._signatures[doc_id] = signature

        # Bucket by band
        for band_idx in range(self._bands):
            start = band_idx * self._rows_per_band
            end = start + self._rows_per_band
            band_signature = tuple(signature.signature[start:end])

            bucket_hash = hash(band_signature)

            if bucket_hash not in self._buckets[band_idx]:
                self._buckets[band_idx][bucket_hash] = []

            bucket = self._buckets[band_idx][bucket_hash]
            if doc_id not in bucket:
                bucket.append(doc_id)

    def find_similar(self, doc_id: str, text: str) -> list[SimilarPair]:
        """Find similar documents.

        Args:
            doc_id: Target document ID
            text: Document text

        Returns:
            List of similar document pairs
        """
        signature = self._min_hasher.compute_signature(doc_id, text)
        candidates: set[str] = set()

        # Collect candidates from each band
        for band_idx in range(self._bands):
            start = band_idx * self._rows_per_band
            end = start + self._rows_per_band
            band_signature = tuple(signature.signature[start:end])
            bucket_hash = hash(band_signature)

            if bucket_hash in self._buckets[band_idx]:
                for candidate_id in self._buckets[band_idx][bucket_hash]:
                    if candidate_id != doc_id:
                        candidates.add(candidate_id)

        # Compute actual similarity for candidates
        similar_pairs: list[SimilarPair] = []

        for candidate_id in candidates:
            candidate_sig = self._signatures.get(candidate_id)
            if candidate_sig:
                similarity = MinHasher.estimate_similarity(signature, candidate_sig)
                similar_pairs.append(
                    SimilarPair(
                        doc_id_1=doc_id,
                        doc_id_2=candidate_id,
                        estimated_similarity=similarity,
                    )
                )

        # Sort by similarity descending
        similar_pairs.sort(key=lambda p: p.estimated_similarity, reverse=True)

        return similar_pairs

    def _find_band_candidates(self, doc_id: str, signature) -> set[str]:
        """Find candidate doc IDs by checking LSH band buckets."""
        candidates: set[str] = set()
        for band_idx in range(self._bands):
            start = band_idx * self._rows_per_band
            end = start + self._rows_per_band
            band_signature = tuple(signature.signature[start:end])
            bucket_hash = hash(band_signature)

            if bucket_hash in self._buckets[band_idx]:
                for candidate_id in self._buckets[band_idx][bucket_hash]:
                    if candidate_id != doc_id:
                        candidates.add(candidate_id)
        return candidates

    def find_duplicates(self, threshold: float = 0.8) -> list[SimilarPair]:
        """Find all duplicate document pairs.

        Args:
            threshold: Duplicate threshold

        Returns:
            Duplicate document pairs
        """
        seen_pairs: set[tuple[str, str]] = set()
        duplicates: list[SimilarPair] = []

        for doc_id, signature in self._signatures.items():
            candidates = self._find_band_candidates(doc_id, signature)

            for candidate_id in candidates:
                pair_key = tuple(sorted([doc_id, candidate_id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                candidate_sig = self._signatures.get(candidate_id)
                if not candidate_sig:
                    continue
                similarity = MinHasher.estimate_similarity(signature, candidate_sig)
                if similarity >= threshold:
                    duplicates.append(
                        SimilarPair(
                            doc_id_1=doc_id,
                            doc_id_2=candidate_id,
                            estimated_similarity=similarity,
                        )
                    )

        duplicates.sort(key=lambda p: p.estimated_similarity, reverse=True)
        return duplicates

    @property
    def document_count(self) -> int:
        """Number of stored documents."""
        return len(self._signatures)

    def clear(self) -> None:
        """Reset all state."""
        self._buckets = [{} for _ in range(self._bands)]
        self._signatures.clear()

    def to_dict(self) -> dict[str, Any]:
        """State information."""
        return {
            "num_hashes": self._num_hashes,
            "bands": self._bands,
            "rows_per_band": self._rows_per_band,
            "document_count": self.document_count,
        }
