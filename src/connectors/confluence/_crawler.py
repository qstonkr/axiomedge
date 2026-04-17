"""Confluence crawl strategies (DFS, BFS, flat, CQL enumeration).

Provides :class:`CrawlerMixin` with child-page discovery and three crawl
modes: recursive DFS, BFS with asyncio.Queue, and flat list processing.
Extracted from ``client.py`` for SRP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .models import FullPageContent

logger = logging.getLogger(__name__)


class CrawlerMixin:
    """Crawl strategies mixed into :class:`ConfluenceFullClient`.

    Host class must provide: ``base_url``, ``shutdown_requested``,
    ``visited_pages``, ``_total_pages_crawled``, ``_max_concurrent``,
    ``_page_sem``, ``_http_get_with_retry``, ``_process_single_page``,
    ``get_child_pages``.
    """

    # ------------------------------------------------------------------
    # Page validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_page_content(page: FullPageContent, page_id: str) -> None:
        """Log warnings for pages with empty or suspicious content."""
        text_len = len(page.content_text) if page.content_text else 0
        html_len = len(page.content_html) if page.content_html else 0
        if text_len > 0:
            return
        if html_len == 0:
            logger.warning(
                "Page %s (%s) has empty body (html=0, text=0) "
                "-- possible permission issue",
                page_id,
                page.title[:50],
            )
        else:
            logger.warning(
                "Page %s (%s) has HTML (%d chars) but extracted text is empty",
                page_id,
                page.title[:50],
                html_len,
            )

    # ------------------------------------------------------------------
    # Child page discovery
    # ------------------------------------------------------------------

    async def get_child_pages(self, page_id: str) -> list[str]:
        """Return child page IDs with pagination."""
        url: str | None = (
            f"{self.base_url}/rest/api/content/{page_id}/child/page"
        )
        params: dict[str, Any] = {"limit": 100}
        child_ids: list[str] = []
        visited_urls: set[str] = set()

        try:
            while url:
                if url in visited_urls:
                    break
                visited_urls.add(url)

                response = await self._http_get_with_retry(url, params=params)
                data = response.json()

                results = data.get("results", [])
                if not results:
                    break

                for result in results:
                    child_ids.append(result.get("id"))

                links = data.get("_links", {})
                next_link = links.get("next")
                if next_link:
                    url = f"{self.base_url}{next_link}"
                    params = {}
                else:
                    url = None
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(
                "Could not fetch children of %s: %s", page_id, e
            )

        return child_ids

    async def _fetch_cql_page(
        self, url: str, params: dict
    ) -> tuple[list[dict], int] | None:
        """Fetch a single CQL search page. Returns (results, totalSize) or None."""
        try:
            resp = await self._http_get_with_retry(url, params=params)
            data = resp.json()
            return data.get("results", []), data.get("totalSize", 0)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("CQL search error (start=%s): %s", params.get("start"), e)
            return None

    async def get_all_descendant_page_ids_via_cql(
        self, root_page_id: str
    ) -> set[str]:
        """Collect all descendant page IDs under *root_page_id* using CQL."""
        url = f"{self.base_url}/rest/api/content/search"
        cql = f"ancestor={root_page_id} and type=page"
        start = 0
        limit = 100
        all_ids: set[str] = set()

        while not self.shutdown_requested:
            page_result = await self._fetch_cql_page(
                url, {"cql": cql, "limit": limit, "start": start},
            )
            if page_result is None:
                break

            results, total_size = page_result
            if not results:
                break

            for r in results:
                pid = r.get("id")
                if pid:
                    all_ids.add(str(pid))

            start += limit
            if start % 1000 == 0:
                logger.info("CQL enumeration: %d/%d pages", len(all_ids), total_size)
            if start >= total_size:
                break

        logger.info("CQL enumeration complete: %d pages", len(all_ids))
        return all_ids

    # ------------------------------------------------------------------
    # Crawl helpers
    # ------------------------------------------------------------------

    def _should_stop_crawl(self, max_pages: int | None) -> bool:
        """Return True when crawling should stop (shutdown or page limit)."""
        return self.shutdown_requested or (
            bool(max_pages) and self._total_pages_crawled >= max_pages  # type: ignore[operator]
        )

    async def _resume_visited_children(
        self,
        page_id: str,
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """For already-visited pages, explore children so resume works."""
        if depth > max_depth:
            return
        try:
            child_ids = await self.get_child_pages(page_id)
            if child_ids:
                await self._crawl_children(
                    child_ids, depth, max_depth, max_pages,
                    download_attachments, max_attachments_per_page,
                    progress, task_id, source_key,
                )
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # Recursive DFS crawl
    # ------------------------------------------------------------------

    async def crawl_recursive(
        self,
        page_id: str,
        depth: int = 0,
        max_depth: int = 10,
        max_pages: int | None = None,
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        progress: Any | None = None,
        task_id: Any = None,
        source_key: str = "unknown",
    ) -> FullPageContent | None:
        """Recursive DFS crawl with optional parallelism."""

        if self.shutdown_requested:
            return None

        # Already visited: skip content, but still explore children for resume
        if page_id in self.visited_pages:
            await self._resume_visited_children(
                page_id, depth, max_depth, max_pages,
                download_attachments, max_attachments_per_page,
                progress, task_id, source_key,
            )
            return None
        self.visited_pages.add(page_id)

        if self._should_stop_crawl(max_pages) or depth > max_depth:
            return None

        # Phase 1: process page (semaphore-protected HTTP)
        page, child_ids = await self._process_single_page(
            page_id, download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )

        # Phase 2: crawl children (outside semaphore)
        await self._crawl_children(
            child_ids, depth, max_depth, max_pages,
            download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )

        return page

    # ------------------------------------------------------------------
    # BFS crawl
    # ------------------------------------------------------------------

    async def _bfs_process_item(
        self,
        page_id: str,
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        source_key: str,
        queue: asyncio.Queue[tuple[str, int]],
    ) -> bool:
        """Process one BFS queue item. Returns False to stop the worker."""
        if self.shutdown_requested:
            return False
        if page_id in self.visited_pages:
            return True
        self.visited_pages.add(page_id)

        if max_pages and self._total_pages_crawled >= max_pages:
            return False
        if depth > max_depth:
            return True

        _, child_ids = await self._process_single_page(
            page_id, download_attachments, max_attachments_per_page,
            None, None, source_key,
        )
        for child_id in child_ids:
            if child_id not in self.visited_pages:
                await queue.put((child_id, depth + 1))
        return True

    async def crawl_bfs(
        self,
        root_page_id: str,
        max_depth: int = 10,
        max_pages: int | None = None,
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        source_key: str = "unknown",
    ) -> None:
        """BFS crawl using asyncio.Queue for better parallelism.

        Unlike crawl_recursive (DFS with gather), BFS flattens the task tree
        so workers can process pages from any level concurrently.
        """
        queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        await queue.put((root_page_id, 0))

        async def worker() -> None:
            while True:
                try:
                    page_id, depth = await asyncio.wait_for(
                        queue.get(), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    break

                keep_going = await self._bfs_process_item(
                    page_id, depth, max_depth, max_pages,
                    download_attachments, max_attachments_per_page,
                    source_key, queue,
                )
                queue.task_done()
                if not keep_going:
                    break

        workers = [
            asyncio.create_task(worker()) for _ in range(self._max_concurrent)
        ]
        await queue.join()
        for w in workers:
            w.cancel()

    # ------------------------------------------------------------------
    # Child crawl dispatch (parallel vs sequential)
    # ------------------------------------------------------------------

    async def _crawl_children_parallel(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl children concurrently via asyncio.gather."""
        tasks: list[asyncio.Task[FullPageContent | None]] = []
        for child_id in child_ids:
            if self._should_stop_crawl(max_pages):
                break
            tasks.append(
                asyncio.ensure_future(
                    self.crawl_recursive(
                        child_id,
                        depth=depth + 1,
                        max_depth=max_depth,
                        max_pages=max_pages,
                        download_attachments=download_attachments,
                        max_attachments_per_page=max_attachments_per_page,
                        progress=progress,
                        task_id=task_id,
                        source_key=source_key,
                    )
                )
            )

        if not tasks:
            return

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                logger.warning("Child page crawl error: %s", r)

    async def _crawl_children_sequential(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl children one at a time."""
        for child_id in child_ids:
            if self._should_stop_crawl(max_pages):
                break
            await self.crawl_recursive(
                child_id,
                depth=depth + 1,
                max_depth=max_depth,
                max_pages=max_pages,
                download_attachments=download_attachments,
                max_attachments_per_page=max_attachments_per_page,
                progress=progress,
                task_id=task_id,
                source_key=source_key,
            )

    async def _crawl_children(
        self,
        child_ids: list[str],
        depth: int,
        max_depth: int,
        max_pages: int | None,
        download_attachments: bool,
        max_attachments_per_page: int,
        progress: Any | None,
        task_id: Any,
        source_key: str,
    ) -> None:
        """Crawl child pages (parallel or sequential).

        When ``_max_concurrent > 1``, uses ``asyncio.gather`` for parallelism.
        The semaphore (``_page_sem``) controls overall concurrent HTTP calls,
        so nested gather calls remain safe for the Confluence server.
        """
        if not child_ids or self.shutdown_requested:
            return

        args = (
            child_ids, depth, max_depth, max_pages,
            download_attachments, max_attachments_per_page,
            progress, task_id, source_key,
        )
        if self._max_concurrent > 1:
            await self._crawl_children_parallel(*args)
        else:
            await self._crawl_children_sequential(*args)

    # ------------------------------------------------------------------
    # Flat crawl (no recursion)
    # ------------------------------------------------------------------

    async def crawl_flat(
        self,
        page_ids: list[str],
        download_attachments: bool = True,
        max_attachments_per_page: int = 20,
        max_pages: int | None = None,
        progress: Any | None = None,
        task_id: Any = None,
        source_key: str = "unknown",
    ) -> None:
        """Crawl a flat list of page IDs (no recursive descent)."""
        total = len(page_ids)
        for i, page_id in enumerate(page_ids):
            if self.shutdown_requested:
                break
            if max_pages and self._total_pages_crawled >= max_pages:
                break
            if page_id in self.visited_pages:
                continue

            self.visited_pages.add(page_id)

            await self._process_single_page(
                page_id,
                download_attachments,
                max_attachments_per_page,
                progress,
                task_id,
                source_key,
                skip_children=True,
            )

            if progress and task_id and (i + 1) % 50 == 0:
                progress.update(
                    task_id,
                    description=(
                        f"flat crawl: {self._total_pages_crawled}/{total}"
                    ),
                )
