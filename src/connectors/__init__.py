"""Knowledge connectors for local ingestion."""

from .crawl_result import CrawlResultConnector
from .file_upload import FileUploadConnector
from .git import GitConnector, GitConnectorConfig
from .notion import NotionConnector, NotionConnectorConfig
from .onedrive import OneDriveConnector, OneDriveConnectorConfig
from .sharepoint import SharePointConnector, SharePointConnectorConfig
from .slack import SlackConnector, SlackConnectorConfig
from .teams import TeamsConnector, TeamsConnectorConfig

__all__ = [
    "CrawlResultConnector",
    "FileUploadConnector",
    "GitConnector",
    "GitConnectorConfig",
    "NotionConnector",
    "NotionConnectorConfig",
    "OneDriveConnector",
    "OneDriveConnectorConfig",
    "SharePointConnector",
    "SharePointConnectorConfig",
    "SlackConnector",
    "SlackConnectorConfig",
    "TeamsConnector",
    "TeamsConnectorConfig",
]
