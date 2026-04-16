"""Rule-based knowledge graph extraction for Korean legal documents.

For a normalized legal corpus like legalize-kr where law names and article
references follow strict conventions (「법령명」 제N조 제M항 제K호), regex
extraction is both cheaper and more accurate than an LLM-based GraphRAG
pipeline. This package reuses :class:`GraphRAGExtractor`'s Neo4j persistence
layer (``save_to_neo4j`` and friends) and only replaces the extraction step.
"""

from .extractor import LegalGraphExtractor

__all__ = ["LegalGraphExtractor"]
