"""CLI: Crawl knowledge sources.

For local use, this provides a simple file-based crawl.
Reads files from a directory and outputs crawl results as JSON.

Usage:
    python -m cli.crawl --source ./docs/ --output ./crawl_results/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".json", ".yaml", ".yml"}


def crawl_directory(source_dir: str, output_dir: str):
    """Crawl files from source directory and write crawl results."""
    source = Path(source_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if not source.is_dir():
        logger.error("Source directory not found: %s", source_dir)
        return

    results = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        stat = path.stat()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()

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
                "relative_path": str(path.relative_to(source)),
                "crawl_source": "local_filesystem",
            },
        }
        results.append(doc)

    # Write as JSONL
    output_file = output / "crawl_results.jsonl"
    with open(output_file, "w", encoding="utf-8") as f:
        for doc in results:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    logger.info("Crawled %d files from %s -> %s", len(results), source_dir, output_file)


def main():
    parser = argparse.ArgumentParser(description="Knowledge Crawl CLI")
    parser.add_argument("--source", required=True, help="Source directory to crawl")
    parser.add_argument("--output", default="./crawl_results", help="Output directory for crawl results")

    args = parser.parse_args()
    crawl_directory(args.source, args.output)


if __name__ == "__main__":
    main()
