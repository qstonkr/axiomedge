"""Confluence crawl checkpoint & incremental JSONL persistence.

Extracted from client.py as a mixin for SRP.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import page_to_dict

logger = logging.getLogger(__name__)


class CheckpointMixin:
    """Checkpoint & incremental persistence methods.

    Host class must have: output_dir, visited_pages, all_pages,
    _total_pages_crawled, kb_id, _attachment_stats, _page_sem.
    """

    # Runtime stats helpers
    # ------------------------------------------------------------------

    def runtime_stats_path(self) -> Path:
        return self.checkpoint_dir / "crawl_runtime_stats.json"

    def _record_attachment_stats(self, attachment: AttachmentContent) -> None:
        stats = self._runtime_stats
        stats["attachments_total"] += 1
        stats["native_text_chars_total"] += attachment.native_text_chars or 0
        stats["ocr_text_chars_total"] += attachment.ocr_text_chars or 0

        if attachment.ocr_applied:
            stats["attachments_ocr_applied"] += 1
        elif attachment.ocr_units_deferred > 0:
            stats["attachments_ocr_deferred"] += 1
        elif attachment.ocr_skip_reason and attachment.ocr_skip_reason not in {
            "ocr_failed",
            "parse_error",
        }:
            stats["attachments_ocr_skipped"] += 1

        media_type = (attachment.media_type or "").lower()
        if "pdf" in media_type:
            stats["pdf_pages_ocr_attempted"] += attachment.ocr_units_attempted or 0
            stats["pdf_pages_ocr_deferred"] += attachment.ocr_units_deferred or 0
        elif "presentation" in media_type or "powerpoint" in media_type:
            stats["ppt_slides_ocr_attempted"] += attachment.ocr_units_attempted or 0
            stats["ppt_slides_ocr_deferred"] += attachment.ocr_units_deferred or 0

    def write_runtime_stats(self) -> None:
        payload = {
            **self._runtime_stats,
            "pages_total": self._total_pages_crawled,
            "elapsed_seconds": round(time.monotonic() - self._started_at, 2),
        }
        with open(self.runtime_stats_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Checkpoint persistence (local only)
    # ------------------------------------------------------------------

    def save_checkpoint(self, source_key: str) -> None:
        """Save current crawl progress to a local checkpoint file."""
        checkpoint_data = {
            "source_key": source_key,
            "kb_id": self.kb_id,
            "visited_pages": list(self.visited_pages),
            "pages_count": self._total_pages_crawled,
            "last_page_id": self.all_pages[-1].page_id if self.all_pages else None,
            "last_page_title": self.all_pages[-1].title if self.all_pages else None,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        # Atomic write via temp file
        temp_file = self.checkpoint_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        temp_file.rename(self.checkpoint_file)

        logger.info("Checkpoint saved: %d pages", len(self.visited_pages))

    def load_checkpoint(self, source_key: str) -> bool:
        """Restore crawl state from a local checkpoint file."""
        checkpoint_data = None

        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint_data = json.load(f)
            except Exception:  # noqa: BLE001
                checkpoint_data = None

        if checkpoint_data is None:
            return False

        try:
            # Verify same source
            if checkpoint_data.get("source_key") != source_key:
                logger.warning(
                    "Checkpoint belongs to a different source (%s). Ignoring.",
                    checkpoint_data.get("source_key"),
                )
                return False

            # KB ID mismatch guard
            saved_kb_id = checkpoint_data.get("kb_id", "")
            if saved_kb_id and self.kb_id and saved_kb_id != self.kb_id:
                logger.warning(
                    "Checkpoint KB mismatch (%s != %s). Starting fresh.",
                    saved_kb_id,
                    self.kb_id,
                )
                self.visited_pages.clear()
                return False

            # Restore visited pages (merge with already-loaded incremental data)
            checkpoint_visited = set(checkpoint_data.get("visited_pages", []))
            self.visited_pages = self.visited_pages | checkpoint_visited
            self._total_pages_crawled = checkpoint_data.get("pages_count", 0)

            saved_at = checkpoint_data.get("saved_at", "unknown")
            last_title = checkpoint_data.get("last_page_title", "unknown")
            pages_count = checkpoint_data.get("pages_count", 0)

            logger.info(
                "Resuming from checkpoint: saved_at=%s, pages=%d, last_page=%s, "
                "skip=%d",
                saved_at,
                pages_count,
                last_title,
                len(self.visited_pages),
            )

            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to load checkpoint: %s", e)
            return False

    def clear_checkpoint(self) -> None:
        """Delete the checkpoint file."""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
            logger.info("Checkpoint deleted")

    # ------------------------------------------------------------------
    # Incremental JSONL persistence
    # ------------------------------------------------------------------

    def _get_incremental_path(self, source_key: str) -> Path:
        safe_key = re.sub(r"[^\w]", "_", source_key)
        return self.checkpoint_dir / f"incremental_{safe_key}.jsonl"

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def request_shutdown(self) -> None:
        """Set the shutdown flag to gracefully stop crawling."""
        self._shutdown_requested = True

    def _truncate_partial_jsonl_tail(self, jsonl_path: Path) -> None:
        """Remove the last incomplete line from a JSONL file after a write failure."""
        if not jsonl_path.exists():
            return

        try:
            with open(jsonl_path, "rb+") as f:
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                if file_size == 0:
                    return

                f.seek(file_size - 1)
                if f.read(1) == b"\n":
                    return

                window_size = min(file_size, 8192)
                f.seek(file_size - window_size)
                tail = f.read(window_size)
                last_newline = tail.rfind(b"\n")

                if last_newline == -1:
                    f.seek(0)
                    f.truncate(0)
                else:
                    truncate_pos = file_size - window_size + last_newline + 1
                    f.seek(truncate_pos)
                    f.truncate()

            logger.warning("Repaired incomplete trailing line in incremental file.")
        except Exception as repair_error:  # noqa: BLE001
            logger.warning("Incremental file repair failed: %s", repair_error)

    def save_incremental(self, source_key: str) -> None:
        """Append newly crawled pages to a JSONL file and free memory."""
        new_pages = self.all_pages[self._incremental_saved_count:]
        if not new_pages:
            return

        jsonl_path = self._get_incremental_path(source_key)
        payload_lines = [
            json.dumps(page_to_dict(page), ensure_ascii=False) + "\n"
            for page in new_pages
        ]

        try:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.writelines(payload_lines)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            self._truncate_partial_jsonl_tail(jsonl_path)
            raise

        # Free memory: all pages have been flushed to JSONL on disk.
        self.all_pages.clear()
        self._incremental_saved_count = 0
        self.write_runtime_stats()
        logger.info(
            "Incremental save: %d pages added (total %d, memory freed)",
            len(new_pages),
            self._total_pages_crawled,
        )

    def load_incremental(self, source_key: str) -> int:
        """Load previously saved pages from a JSONL file.

        Only pages with non-empty content_text are marked as visited.
        Pages with empty content (e.g., index/TOC pages, failed extractions)
        are left unvisited so they get re-crawled on resume.
        """
        jsonl_path = self._get_incremental_path(source_key)
        if not jsonl_path.exists():
            return 0

        loaded = 0
        skipped_empty = 0
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    page_data = json.loads(line)
                    page_id = page_data.get("page_id", "")
                    if not page_id:
                        continue
                    content_text = page_data.get("content_text", "")
                    if content_text and len(content_text.strip()) > 0:
                        self.visited_pages.add(page_id)
                        loaded += 1
                    else:
                        skipped_empty += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Error loading incremental file: %s", e)

        if loaded > 0 or skipped_empty > 0:
            logger.info(
                "Incremental file: %d pages marked visited, %d pages to re-crawl",
                loaded,
                skipped_empty,
            )
            self._incremental_saved_count = 0
        return loaded

    def clear_incremental(self, source_key: str) -> None:
        """Delete the incremental JSONL file."""
        jsonl_path = self._get_incremental_path(source_key)
        if jsonl_path.exists():
            jsonl_path.unlink()

    def finalize_from_incremental(self, source_key: str) -> list[dict]:
        """Merge incremental JSONL and in-memory pages into a final list of dicts."""
        all_page_dicts: list[dict] = []
        seen_ids: set[str] = set()

        # 1. Load from JSONL
        jsonl_path = self._get_incremental_path(source_key)
        if jsonl_path.exists():
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    page_data = json.loads(line)
                    pid = page_data.get("page_id", "")
                    if pid and pid not in seen_ids:
                        all_page_dicts.append(page_data)
                        seen_ids.add(pid)

        # 2. Append unsaved in-memory pages
        for p in self.all_pages:
            if p.page_id not in seen_ids:
                all_page_dicts.append(page_to_dict(p))
                seen_ids.add(p.page_id)

        return all_page_dicts

    # ------------------------------------------------------------------
