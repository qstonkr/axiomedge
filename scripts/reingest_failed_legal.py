#!/usr/bin/env python3
"""Targeted re-ingestion of legal-kr docs that failed in the main run.

Supplementary tool, run after the main legal-kr ingestion finishes to
retry docs that hit httpx.ReadTimeout in the live pipeline (typically
the largest statutes, which overwhelm the 180s TEI timeout when the
live semaphore lets several of them contend for TEI at once).

Why it exists: the live API runs with a shared TEI httpx timeout and
Semaphore(8). Large statutes (e.g. 조세특례제한법/시행령 at 1.9MB)
occasionally timeout under contention. A full data-source re-trigger
would scan all 6,907 docs; this script isolates just the failed ones.

Strategy:
    - Spin up a standalone IngestionPipeline in this process, reusing
      the same docker services (Qdrant, Neo4j, Postgres, Redis) the
      running API is bound to. No API restart required.
    - Override the embedder with a fresh TEIEmbeddingProvider that has
      a longer httpx timeout so large statutes don't trip the 180s
      ceiling that caused the original failure.
    - Process failed docs sequentially (no semaphore contention), so
      TEI has all of its capacity for the current batch.

Safety:
    - Hardcoded KB_ID='legal-kr' — all writes stay inside kb_legal_kr.
    - Preflight refuses to run while the main ingestion is still
      'running' / 'pending' / 'syncing' (use --force to override).
    - Chunk point_ids are deterministic (kb:doc_id:idx) so a re-run
      upserts the same IDs without duplicating. Neo4j writes use MERGE.
    - ensure_collection is idempotent and never deletes a populated
      collection.

Usage:
    uv run python scripts/reingest_failed_legal.py --dry-run
    uv run python scripts/reingest_failed_legal.py
    uv run python scripts/reingest_failed_legal.py --files \\
        "kr/개발이익환수에관한법률/시행령.md,kr/개별소비세법/법률.md"
    uv run python scripts/reingest_failed_legal.py --log /private/tmp/api.log
    uv run python scripts/reingest_failed_legal.py --timeout 900 -y

Failure parsing: reads the API log (default /private/tmp/api.log) and
extracts relative paths from lines matching
`Ingestion pipeline failed for doc_id=git:legalize-kr:...`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

# Make src imports work when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Env sanity — don't clobber whatever the caller already set
os.environ.setdefault("BGE_TEI_URL", "http://54.180.231.139:8080")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_AUTH", "none")

REPO_ROOT = Path.home() / ".knowledge-local" / "git_repos" / "9297b14b-c2ed-4e60-97d3-6a6588445de3"
KB_ID = "legal-kr"
REPO_URL = "https://github.com/legalize-kr/legalize-kr"
DEFAULT_LOG = "/private/tmp/api.log"


def parse_failed_docs_from_log(log_path: str) -> list[str]:
    """Extract relative paths from 'Ingestion pipeline failed' lines."""
    if not Path(log_path).exists():
        print(f"[warn] log not found: {log_path}", file=sys.stderr)
        return []
    rels: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"doc_id=git:legalize-kr:(.+?)(?:\"|\s|$)")
    with open(log_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"level": "ERROR"' not in line:
                continue
            if "Ingestion pipeline failed" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message", "")
            m = pattern.search(msg)
            if not m:
                continue
            rel = m.group(1).strip()
            if rel and rel not in seen:
                seen.add(rel)
                rels.append(rel)
    return rels


def find_unprocessed_files() -> list[str]:
    """Compare repo files against Qdrant doc_ids to find unprocessed files.

    Queries Qdrant for all doc_ids in kb_legal_kr, then diffs against
    the full file list in REPO_ROOT/kr/ to find files that were never
    ingested (due to mid-run termination or dedup skip).
    """
    import httpx

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")

    # 1. Get all unique doc_ids from Qdrant via scroll
    stored_doc_ids: set[str] = set()
    offset = None
    while True:
        body: dict = {"limit": 1000, "with_payload": {"include": ["doc_id"]}, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        resp = httpx.post(
            f"{qdrant_url}/collections/kb_legal_kr/points/scroll",
            json=body, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["result"]
        points = data.get("points", [])
        if not points:
            break
        for p in points:
            doc_id = (p.get("payload") or {}).get("doc_id", "")
            if doc_id:
                stored_doc_ids.add(doc_id)
        offset = data.get("next_page_offset")
        if offset is None:
            break

    print(f"  Qdrant: {len(stored_doc_ids)} unique doc_ids in kb_legal_kr")

    # 2. List all .md files in repo
    repo_kr = REPO_ROOT / "kr"
    all_files: list[str] = []
    for md_file in repo_kr.rglob("*.md"):
        rel = str(md_file.relative_to(REPO_ROOT))
        all_files.append(rel)

    print(f"  Repo:   {len(all_files)} .md files in kr/")

    # 3. Diff — files whose doc_id is NOT in Qdrant
    unprocessed: list[str] = []
    for rel in all_files:
        doc_id = f"git:legalize-kr:{rel}"
        if doc_id not in stored_doc_ids:
            unprocessed.append(rel)

    print(f"  Unprocessed: {len(unprocessed)} files")
    return unprocessed


def build_raw_document(rel_path: str):
    """Mirror GitConnector._build_documents for a single file.

    Re-uses the same frontmatter promotion and legal metadata flags so
    the pipeline routes through LegalGraphExtractor.
    """
    from src.connectors.git.frontmatter import parse_frontmatter, promote_legal_metadata
    from src.core.models import RawDocument

    abs_path = REPO_ROOT / rel_path
    if not abs_path.is_file():
        raise FileNotFoundError(f"missing: {abs_path}")

    raw = abs_path.read_text(encoding="utf-8", errors="ignore")
    frontmatter, body = parse_frontmatter(raw)
    body = body.strip()
    if not body:
        raise ValueError(f"empty body: {abs_path}")

    metadata: dict = {
        "source_type": "git",
        "repo_url": REPO_URL,
        "branch": "main",
        "file_path": rel_path,
        "file_ext": abs_path.suffix.lower(),
        "file_size_bytes": abs_path.stat().st_size,
        "commit_sha": "reingest",
        "commit_date": "",
        "knowledge_type": "legal-kr",
    }
    legal_meta = promote_legal_metadata(frontmatter)
    if legal_meta:
        metadata.update(legal_meta)
        parts = rel_path.split("/")
        if len(parts) >= 2 and parts[0] == "kr":
            metadata["parent_law_slug"] = parts[1]
        stem = abs_path.stem
        if "법률" in stem:
            metadata["law_file_kind"] = "law"
        elif "시행령" in stem:
            metadata["law_file_kind"] = "decree"
        elif "시행규칙" in stem:
            metadata["law_file_kind"] = "rule"
        else:
            metadata["law_file_kind"] = "other"

    return RawDocument(
        doc_id=f"git:legalize-kr:{rel_path}",
        title=str(metadata.get("law_name") or abs_path.stem),
        content=body,
        source_uri=f"{REPO_URL}/blob/main/{rel_path}",
        author=metadata.get("ministry", ""),
        updated_at=None,
        content_hash=RawDocument.sha256(body),
        metadata=metadata,
    )


async def init_pipeline(tei_timeout: float):
    """Build an IngestionPipeline bound to the running docker services.

    Bypasses the running API process entirely. Uses its own TEI client
    with the larger timeout, so big statutes have more headroom than the
    live API's 180s default.
    """
    from src.api.state import AppState
    from src.config import settings as _settings

    # Reuse the API's private init helpers — they populate `state` with
    # qdrant_store / graph_repo / dedup_cache / term_extractor. We skip
    # search-side services (answer generator, reranker, etc.) because
    # ingestion doesn't need them.
    from src.api.app import (
        _init_vectordb,
        _init_graph,
        _init_dedup,
    )

    state = AppState()
    settings = _settings

    await _init_vectordb(state, settings)
    await _init_graph(state, settings)
    await _init_dedup(state, settings)

    # Override the embedder with a long-timeout client. The live API's
    # embedder uses 180s; large statutes with many chunks blew past that.
    from src.nlp.embedding.tei_provider import TEIEmbeddingProvider

    embedder = TEIEmbeddingProvider(timeout=tei_timeout)
    if not embedder.is_ready():
        raise RuntimeError("TEI server not reachable")
    state["embedder"] = embedder

    # Rule-based legal extractor — cheap, reuses the Neo4j driver.
    from src.pipeline.legal_graph import LegalGraphExtractor

    legal_graph_extractor = LegalGraphExtractor()

    # Term extractor uses the embedder for similarity.
    from src.pipeline.term_extractor import TermExtractor

    term_extractor = TermExtractor(embedder=embedder)

    # Sparse embedder: adapter wrapping the same TEI provider so the
    # pipeline can call embed_sparse() without rebuilding logic.
    class _SparseEmbedder:
        def __init__(self, e):
            self._e = e

        async def embed_sparse(self, texts):
            out = await asyncio.to_thread(self._e.encode, texts, False, True, False)
            return out.get("lexical_weights", [{} for _ in texts])

    sparse_embedder = _SparseEmbedder(embedder)

    # Ensure the target collection exists.
    collections = state.get("qdrant_collections")
    if collections is not None:
        await collections.ensure_collection(KB_ID)

    from src.pipeline.ingestion import IngestionPipeline

    pipeline = IngestionPipeline(
        embedder=embedder,
        sparse_embedder=sparse_embedder,
        vector_store=state.get("qdrant_store"),
        graph_store=state.get("graph_repo"),
        dedup_cache=state.get("dedup_cache"),
        dedup_pipeline=state.get("dedup_pipeline"),
        enable_ingestion_gate=True,
        enable_term_extraction=True,
        enable_graphrag=True,
        term_extractor=term_extractor,
        graphrag_extractor=None,
        legal_graph_extractor=legal_graph_extractor,
    )
    return pipeline, state


async def preflight_check(allow_force: bool) -> tuple[bool, str]:
    """Refuse to run if the main legal-kr ingestion is still in progress.

    Returns (ok, reason). Safe to run in dry-run mode even if main is
    running — this check is only invoked for real executions.
    """
    import asyncpg

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://knowledge:knowledge@localhost:5432/knowledge_db",
    )
    # asyncpg doesn't accept the +asyncpg dialect prefix
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(db_url, timeout=5)
    except Exception as e:
        return False, f"Cannot reach Postgres to check run status: {e}"

    try:
        row = await conn.fetchrow(
            "SELECT status, chunks_stored, "
            "EXTRACT(EPOCH FROM (NOW()-started_at))::int AS elapsed_s "
            "FROM knowledge_ingestion_runs "
            "WHERE kb_id=$1 ORDER BY started_at DESC LIMIT 1",
            KB_ID,
        )
    finally:
        await conn.close()

    if row is None:
        return True, "no prior run recorded"

    status = row["status"]
    if status in ("running", "pending", "syncing"):
        msg = (
            f"Main ingestion is {status!r} "
            f"(elapsed={row['elapsed_s']}s, chunks_stored={row['chunks_stored']}). "
            "Wait for it to finish before reingesting, or pass --force."
        )
        return allow_force, msg

    return True, f"last run status={status!r}"


async def _collect_rels(args) -> list[str]:
    """Collect file paths from --auto-detect, --file-list, --files, or log parsing."""
    rels: list[str] = []
    if args.auto_detect:
        print("Auto-detecting unprocessed files (Qdrant scan)...")
        rels = find_unprocessed_files()
    elif args.file_list:
        p = Path(args.file_list)
        if not p.exists():
            print(f"[error] file list not found: {p}", file=sys.stderr)
            return []
        rels = [line.strip() for line in p.read_text().splitlines() if line.strip()]
    elif args.files:
        rels = [p.strip() for p in args.files.split(",") if p.strip()]
    else:
        rels = parse_failed_docs_from_log(args.log)
    return rels


async def main_async(args) -> int:
    rels = await _collect_rels(args)

    if not rels:
        print("No failed docs to re-ingest.")
        return 0

    # Validate files exist
    missing = [r for r in rels if not (REPO_ROOT / r).is_file()]
    existing = [r for r in rels if (REPO_ROOT / r).is_file()]
    print(f"Found {len(rels)} doc(s): {len(existing)} exist, {len(missing)} missing")
    if missing and len(missing) <= 20:
        for m in missing:
            print(f"  ✗ MISSING: {m}")
    elif missing:
        print(f"  ✗ {len(missing)} files missing (first 5: {missing[:5]})")
    rels = existing

    if args.dry_run:
        total_bytes = sum((REPO_ROOT / r).stat().st_size for r in rels)
        print(f"\n--dry-run — {len(rels)} files, {total_bytes:,} bytes total")
        return 0

    print("\nPre-flight check: main ingestion status...")
    ok, reason = await preflight_check(allow_force=args.force)
    print(f"  {reason}")
    if not ok:
        print("\n❌ Aborting. Re-run after main ingestion completes, or pass --force.")
        return 3

    concurrency = args.concurrency
    print(f"\nTarget KB:        {KB_ID}")
    print(f"Qdrant collection: kb_{KB_ID.replace('-', '_')}")
    print(f"TEI timeout:       {args.timeout}s")
    print(f"Docs to process:   {len(rels)} (concurrency={concurrency})")
    if not args.yes:
        resp = input("\nProceed? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("Aborted by user.")
            return 4

    print(f"\nInitializing pipeline (TEI timeout={args.timeout}s)...")
    pipeline, state = await init_pipeline(args.timeout)

    successes: list[str] = []
    failures: list[tuple[str, str]] = []
    sem = asyncio.Semaphore(concurrency)
    completed = 0

    async def _ingest_one(rel: str) -> None:
        nonlocal completed
        try:
            raw = build_raw_document(rel)
        except Exception as e:
            print(f"  ✗ {rel} (build failed: {e})")
            failures.append((rel, f"build: {e}"))
            return

        async with sem:
            try:
                result = await pipeline.ingest(raw, collection_name=KB_ID)
                completed += 1
                if getattr(result, "chunks_stored", 0) > 0:
                    print(f"  [{completed}/{len(rels)}] ✓ {rel} chunks={result.chunks_stored}")
                    successes.append(rel)
                else:
                    reason = getattr(result, "reason", "no chunks stored")
                    print(f"  [{completed}/{len(rels)}] ⚠ {rel}: {reason}")
                    failures.append((rel, reason))
            except Exception as e:
                completed += 1
                print(f"  [{completed}/{len(rels)}] ✗ {rel}: {type(e).__name__}: {e}")
                failures.append((rel, f"{type(e).__name__}: {e}"))

    print(f"\nProcessing {len(rels)} docs (concurrency={concurrency})...")
    await asyncio.gather(*[_ingest_one(rel) for rel in rels])

    print()
    print(f"Summary: {len(successes)} success, {len(failures)} failed")
    if failures:
        print("\nStill failing:")
        for rel, reason in failures:
            print(f"  - {rel}: {reason}")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log", default=DEFAULT_LOG,
        help=f"API log file to parse for failures (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--files", default="",
        help="Comma-separated relative paths (overrides --log)",
    )
    parser.add_argument(
        "--file-list", default="",
        help="Path to a text file with one relative path per line (overrides --files and --log)",
    )
    parser.add_argument(
        "--timeout", type=float, default=600.0,
        help="TEI httpx timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=6,
        help="Number of concurrent ingestion tasks (default: 6)",
    )
    parser.add_argument(
        "--auto-detect", action="store_true",
        help="Auto-detect unprocessed files by scanning Qdrant vs repo (overrides all other sources)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List the docs and exit without ingesting",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Override the preflight check (run even if main ingestion is still running)",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
