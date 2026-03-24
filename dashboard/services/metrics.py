"""Knowledge Dashboard Local Metrics Service.

No-op implementation - no StatsD for local development.
All methods are silent stubs.
"""

import logging
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


class DashboardMetrics:
    """No-op metrics for local development."""

    def page_loaded(self, page: str, duration_ms: float) -> None:
        pass

    def search_executed(self, query: str, results: int, duration_ms: float) -> None:
        pass

    def api_call(self, method: str, path: str, status: int, duration_ms: float) -> None:
        pass

    def error(self, page: str, error_type: str) -> None:
        pass

    def graph_query(self, query_type: str, duration_ms: float) -> None:
        pass

    def track_search_quality(self, *, mode: str, has_results: bool, source_count: int,
                             duration_ms: float, has_stale_docs: bool = False,
                             quality_gate_passed: bool | None = None,
                             timed_out: bool = False) -> None:
        pass

    def session_active(self, count: int) -> None:
        pass

    @contextmanager
    def timed(self, metric_name: str, **tags: str) -> Generator[None, None, None]:
        yield


metrics = DashboardMetrics()
