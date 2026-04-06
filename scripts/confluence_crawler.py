#!/usr/bin/env python3
"""Confluence Crawler CLI — thin wrapper around src.connectors.confluence.

Usage:
    uv run python scripts/confluence_crawler.py --page-id 373865276 --full
    uv run python scripts/confluence_crawler.py --source faq --sample 10
    uv run python scripts/confluence_crawler.py --all-sources --full --max-concurrent 3
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.connectors.confluence import CrawlerConfig, crawl_space, save_results
from src.connectors.confluence.output import save_results_from_jsonl


def _setup_logging() -> None:
    try:
        from rich.logging import RichHandler
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            handlers=[RichHandler(rich_tracebacks=True)],
        )
    except ImportError:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )


logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Confluence Crawler — 지식 체계 구축용")
    try:
        sources = list(CrawlerConfig.from_env().knowledge_sources.keys())
    except (ValueError, KeyError):
        sources = None
    parser.add_argument("--source", choices=sources, help="지식 소스 선택")
    parser.add_argument("--page-id", help="직접 페이지 ID 지정")
    parser.add_argument("--all-sources", action="store_true", help="모든 소스 순차 크롤링")
    parser.add_argument("--list-sources", action="store_true", help="소스 목록 출력")
    parser.add_argument("--sample", type=int, default=None, help="샘플 페이지 수")
    parser.add_argument("--full", action="store_true", help="전체 크롤링")
    parser.add_argument("--no-attachments", action="store_true", help="첨부파일 스킵")
    parser.add_argument("--max-attachments", type=int, default=20)
    parser.add_argument("--max-concurrent", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="이전 중단 지점부터 재개")
    parser.add_argument("--fresh-full", action="store_true", help="체크포인트 삭제 후 전체 크롤링")
    parser.add_argument("--kb-id", default="", help="KB ID")
    parser.add_argument("--no-bfs", action="store_true", help="DFS 모드 사용 (기본: BFS)")
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = CrawlerConfig.from_env()

    if args.list_sources:
        for key, info in config.knowledge_sources.items():
            logger.info("  %s: %s (page_id=%s)", key, info["name"], info["page_id"])
        return

    # Determine sources to crawl
    sources_to_crawl: list[tuple[str, str, str]] = []

    if args.page_id:
        name = args.kb_id or f"page-{args.page_id}"
        sources_to_crawl.append((args.page_id, name, name))
    elif args.source:
        info = config.knowledge_sources[args.source]
        sources_to_crawl.append((info["page_id"], info["name"], args.source))
    elif args.all_sources:
        for key, info in config.knowledge_sources.items():
            sources_to_crawl.append((info["page_id"], info["name"], key))
    else:
        logger.error("--source, --page-id, 또는 --all-sources 중 하나를 지정하세요")
        sys.exit(1)

    # --fresh-full: clear all checkpoints and force full crawl
    if args.fresh_full:
        args.full = True
        args.resume = False

    max_pages = None if args.full else (args.sample or 10)
    download_attachments = not args.no_attachments

    for page_id, source_name, source_key in sources_to_crawl:
        logger.info("=" * 60)
        logger.info("Crawling: %s (page_id=%s)", source_name, page_id)

        result = await crawl_space(
            config=config,
            page_id=page_id,
            source_name=source_name,
            source_key=source_key,
            max_pages=max_pages,
            download_attachments=download_attachments,
            max_attachments_per_page=args.max_attachments,
            resume=args.resume,
            max_concurrent=args.max_concurrent,
            kb_id=args.kb_id,
            use_bfs=not args.no_bfs,
            register_signals=True,
        )

        # Save output JSON
        safe_name = re.sub(r"[^\w]", "_", source_name)
        output_path = config.output_dir / f"crawl_{safe_name}.json"

        if result.jsonl_path:
            save_results_from_jsonl(
                Path(result.jsonl_path), output_path,
                source_info={"page_id": page_id, "name": source_name, "key": source_key},
            )
        else:
            save_results(
                result.pages, output_path,
                source_info={"page_id": page_id, "name": source_name, "key": source_key},
                page_dicts=result.page_dicts,
            )

        # Clean up checkpoint on success
        if not result.interrupted:
            checkpoint = config.output_dir / "checkpoint.json"
            if checkpoint.exists():
                import json
                with open(checkpoint) as f:
                    cp = json.load(f)
                if cp.get("source_key") == source_key:
                    checkpoint.unlink()

        logger.info("Output saved: %s", output_path)


def main() -> None:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
