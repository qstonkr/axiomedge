"""CLI: Crawl knowledge sources with incremental support.

Reads files from a directory and outputs crawl results as JSONL.
Supports incremental mode: only processes new/changed files by comparing
content hashes against a state file from the previous crawl.

Usage:
    python -m cli.crawl --source ./docs/ --output ./crawl_results/
    python -m cli.crawl --source ./docs/ --output ./crawl_results/ --full  # Force full re-crawl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".json", ".yaml", ".yml"}
STATE_FILE_NAME = ".crawl_state.json"


def _load_crawl_state(output_dir: Path) -> dict[str, str]:
    """Load previous crawl state (file path -> content_hash mapping)."""
    state_file = output_dir / STATE_FILE_NAME
    if not state_file.exists():
        return {}
    try:
        with open(state_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_crawl_state(output_dir: Path, state: dict[str, str]) -> None:
    """Save crawl state for next incremental run."""
    state_file = output_dir / STATE_FILE_NAME
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def crawl_directory(source_dir: str, output_dir: str, full: bool = False):
    """Crawl files from source directory and write crawl results.

    In incremental mode (default), only new/changed files are processed.
    Changed files are detected by comparing SHA-256 content hashes.
    """
    source = Path(source_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if not source.is_dir():
        logger.error("Source directory not found: %s", source_dir)
        return

    # Load previous state for incremental detection
    prev_state = {} if full else _load_crawl_state(output)
    new_state: dict[str, str] = {}

    results = []
    skipped = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        rel_path = str(path.relative_to(source))
        stat = path.stat()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        # Track state for next run
        new_state[rel_path] = content_hash

        # Incremental: skip if hash unchanged
        if not full and prev_state.get(rel_path) == content_hash:
            skipped += 1
            continue

        # For text files, read content directly
        content = ""
        if path.suffix.lower() in {".txt", ".md", ".json", ".yaml", ".yml"}:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = path.read_text(encoding="euc-kr")
                except Exception:
                    content = f"[Binary file: {path.name}]"

        doc = {
            "doc_id": content_hash[:16],
            "title": path.name,
            "content": content,
            "source_uri": str(path.absolute()),
            "author": "",
            "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "content_hash": content_hash,
            "metadata": {
                "file_name": path.name,
                "file_size": stat.st_size,
                "file_extension": path.suffix.lower(),
                "relative_path": rel_path,
                "crawl_source": "local_filesystem",
            },
        }
        results.append(doc)

    # Write as JSONL
    output_file = output / "crawl_results.jsonl"
    with open(output_file, "w", encoding="utf-8") as f:
        for doc in results:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    # Save state for next incremental run
    _save_crawl_state(output, new_state)

    mode = "FULL" if full else "INCREMENTAL"
    logger.info(
        "[%s] Crawled %d new/changed files, skipped %d unchanged from %s -> %s",
        mode, len(results), skipped, source_dir, output_file,
    )

    # Also detect deleted files
    if not full and prev_state:
        deleted = set(prev_state.keys()) - set(new_state.keys())
        if deleted:
            logger.info("Detected %d deleted files: %s", len(deleted), list(deleted)[:5])


def main():
    parser = argparse.ArgumentParser(description="Knowledge Crawl CLI")
    parser.add_argument("--source", required=True, help="Source directory to crawl")
    parser.add_argument("--output", default="./crawl_results", help="Output directory for crawl results")
    parser.add_argument("--full", action="store_true", help="Force full re-crawl (ignore incremental state)")

    args = parser.parse_args()
    crawl_directory(args.source, args.output, full=args.full)


if __name__ == "__main__":
    main()
