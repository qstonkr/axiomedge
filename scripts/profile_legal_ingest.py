"""Profile single-doc legal ingestion to identify the throughput bottleneck.

Usage:
    uv run python scripts/profile_legal_ingest.py [file_rel_path]

Runs the same pipeline steps as IngestionPipeline.ingest() for a single
legal document from the already-cloned legalize-kr repo, printing wall
time for each step. Services are created directly (no API server).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from src.connectors.git.connector import _law_file_kind  # noqa: F401
from src.connectors.git.frontmatter import parse_frontmatter, promote_legal_metadata
from src.domain.models import RawDocument
from src.pipeline.chunker import Chunker


_REPO = Path.home() / ".knowledge-local/git_repos/9297b14b-c2ed-4e60-97d3-6a6588445de3"
_DEFAULT_FILE = "kr/119구조ㆍ구급에관한법률/법률.md"


class Stopwatch:
    def __init__(self) -> None:
        self.steps: list[tuple[str, float]] = []
        self._t0 = time.perf_counter()

    def mark(self, label: str) -> None:
        t1 = time.perf_counter()
        self.steps.append((label, t1 - self._t0))
        self._t0 = t1

    def report(self) -> None:
        total = sum(dt for _, dt in self.steps)
        print("\n=== Per-step timings ===")
        for label, dt in self.steps:
            pct = 100 * dt / total if total else 0
            bar = "█" * max(1, int(pct / 2))
            print(f"{label:<40s} {dt*1000:>8.1f} ms  {pct:>5.1f}%  {bar}")
        print(f"{'TOTAL':<40s} {total*1000:>8.1f} ms")


async def main() -> None:
    rel = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_FILE
    path = _REPO / rel
    if not path.is_file():
        print(f"File not found: {path}")
        sys.exit(1)

    os.environ.setdefault("NEO4J_PASSWORD", os.getenv("NEO4J_PASSWORD", "knowledge"))

    sw = Stopwatch()

    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    legal_meta = promote_legal_metadata(frontmatter)
    sw.mark("0_read_and_parse_frontmatter")

    metadata = {
        "source_type": "git",
        "repo_url": "https://github.com/legalize-kr/legalize-kr",
        "branch": "main",
        "file_path": rel,
        "file_ext": ".md",
        "commit_sha": "profile",
        "knowledge_type": "profile",
        **legal_meta,
        "parent_law_slug": rel.split("/")[1] if rel.startswith("kr/") else "",
        "law_file_kind": "law",
    }

    raw = RawDocument(
        doc_id=f"profile:{rel}",
        title=str(metadata.get("law_name") or path.stem),
        content=body,
        source_uri=f"https://github.com/legalize-kr/legalize-kr/blob/main/{rel}",
        content_hash=RawDocument.sha256(body),
        metadata=metadata,
    )
    print(f"\ndoc: {raw.title} ({len(body)} chars, {len(body.splitlines())} lines)")
    print(f"is_legal: {metadata.get('_is_legal_document', False)}")

    # Step 1: chunk
    chunker = Chunker()
    chunk_result = chunker.chunk_legal_articles(body)
    sw.mark("1_chunk_legal_articles")
    print(f"  chunks: {chunk_result.total_chunks}")

    # Step 2: embed (cloud TEI — same path the API uses in prod)
    from src.embedding.tei_provider import TEIEmbeddingProvider

    tei_url = os.getenv("BGE_TEI_URL", "http://54.180.231.139:8080")
    embedder = TEIEmbeddingProvider(base_url=tei_url)
    assert embedder.is_ready(), f"TEI not reachable at {tei_url}"
    sw.mark("2_embedder_init")

    chunk_texts = [hc.text for hc in chunk_result.heading_chunks]
    dense = await asyncio.to_thread(
        embedder.encode, chunk_texts, True, False, False,
    )
    dense_vecs = dense.get("dense_vecs", [])
    sw.mark("3_embed_dense_all_chunks")
    print(f"  dense vecs: {len(dense_vecs)} × {len(dense_vecs[0]) if dense_vecs else 0}")

    sparse = await asyncio.to_thread(
        embedder.encode, chunk_texts, False, True, False,
    )
    sw.mark("4_embed_sparse_all_chunks")
    print(f"  sparse vecs: {len(sparse.get('lexical_weights', []))}")

    # Step 3: Neo4j legal graph extraction + save
    from src.pipeline.legal_graph import LegalGraphExtractor

    legal_extractor = LegalGraphExtractor()
    sw.mark("5_legal_extractor_init")

    extraction_result = await legal_extractor.extract_from_document(raw, kb_id="profile")
    sw.mark("6_legal_extract_rule_based")
    print(f"  nodes: {extraction_result.node_count}, rels: {extraction_result.relationship_count}")

    try:
        save_stats = await asyncio.to_thread(
            legal_extractor.save_to_neo4j, extraction_result,
        )
        sw.mark("7_legal_save_to_neo4j")
        print(f"  save_stats: {save_stats}")
    except Exception as e:
        sw.mark("7_legal_save_to_neo4j_FAILED")
        print(f"  save FAILED: {e}")

    # Step 4: term extraction
    try:
        from src.pipeline.term_extractor import TermExtractor

        term_extractor = TermExtractor(embedder=embedder)
        sw.mark("8_term_extractor_init")

        terms = await term_extractor.extract_from_chunks(
            chunk_texts, kb_id="profile",
        )
        sw.mark("9_term_extract_from_chunks")
        print(f"  terms extracted: {len(terms)}")
    except Exception as e:
        sw.mark("9_term_extract_FAILED")
        print(f"  term FAILED: {e}")

    sw.report()


if __name__ == "__main__":
    asyncio.run(main())
