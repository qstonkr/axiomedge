"""Knowledge connectors for local ingestion."""

from .crawl_result import CrawlResultConnector
from .file_upload import FileUploadConnector

__all__ = [
    "CrawlResultConnector",
    "FileUploadConnector",
]
