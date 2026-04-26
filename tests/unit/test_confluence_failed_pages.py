"""Confluence checkpoint — failed_pages 분리 저장 (PR-5 B).

- save 시 failed_pages 가 직렬화됨
- load 시 failed_pages 복원
- visited_pages 와 별도 set 으로 유지 → 재시도 가능
"""

from __future__ import annotations

import json
from pathlib import Path

from src.connectors.confluence._checkpoint import CheckpointMixin


class _Fake(CheckpointMixin):
    """CheckpointMixin 사용을 위해 host class 필드를 최소 구현."""

    def __init__(self, tmp: Path) -> None:
        self.kb_id = "kb-x"
        self.visited_pages: set[str] = set()
        self.failed_pages: set[str] = set()
        self.all_pages = []  # type: ignore[var-annotated]
        self._total_pages_crawled = 0
        self._runtime_stats = {
            "attachments_total": 0, "native_text_chars_total": 0,
            "ocr_text_chars_total": 0, "attachments_ocr_applied": 0,
            "attachments_ocr_deferred": 0, "attachments_ocr_skipped": 0,
            "pdf_pages_ocr_attempted": 0, "pdf_pages_ocr_deferred": 0,
            "ppt_slides_ocr_attempted": 0, "ppt_slides_ocr_deferred": 0,
        }
        self._started_at = 0.0
        self.checkpoint_dir = tmp
        self.checkpoint_file = tmp / "checkpoint.json"
        self.output_dir = tmp


class TestFailedPagesPersistence:
    def test_save_includes_failed_pages(self, tmp_path):
        host = _Fake(tmp_path)
        host.visited_pages = {"v1", "v2"}
        host.failed_pages = {"f1", "f2"}
        host.save_checkpoint(source_key="src-a")

        with open(host.checkpoint_file, encoding="utf-8") as f:
            data = json.load(f)
        assert sorted(data["visited_pages"]) == ["v1", "v2"]
        assert sorted(data["failed_pages"]) == ["f1", "f2"]

    def test_load_restores_failed_pages(self, tmp_path):
        host = _Fake(tmp_path)
        host.visited_pages = {"v1"}
        host.failed_pages = {"f1"}
        host.save_checkpoint(source_key="src-a")

        host2 = _Fake(tmp_path)
        ok = host2.load_checkpoint(source_key="src-a")
        assert ok is True
        assert "f1" in host2.failed_pages
        assert "v1" in host2.visited_pages
        # 둘은 별도의 set
        assert host2.failed_pages != host2.visited_pages

    def test_old_checkpoint_without_failed_pages_loads(self, tmp_path):
        """Backward compatible — old checkpoint 에 failed_pages 키 없으면 빈 set."""
        # 수동으로 v1 만 가진 old checkpoint 작성
        old_data = {
            "source_key": "src-a", "kb_id": "kb-x",
            "visited_pages": ["v1"], "pages_count": 1,
            "last_page_id": None, "last_page_title": None,
            "saved_at": "2026-04-26T00:00:00+00:00",
        }
        host = _Fake(tmp_path)
        host.checkpoint_file.write_text(json.dumps(old_data))

        ok = host.load_checkpoint(source_key="src-a")
        assert ok is True
        assert host.failed_pages == set()
        assert "v1" in host.visited_pages
