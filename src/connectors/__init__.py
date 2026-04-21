"""Knowledge connectors for local ingestion."""

from .crawl_result import CrawlResultConnector
from .file_upload import FileUploadConnector
from .git import GitConnector, GitConnectorConfig
from .gmail import GmailConnector, GmailConnectorConfig
from .google_drive import GoogleDriveConnector, GoogleDriveConnectorConfig
from .google_sheets import GoogleSheetsConnector, GoogleSheetsConnectorConfig
from .jira import JiraConnector, JiraConnectorConfig
from .notion import NotionConnector, NotionConnectorConfig
from .onedrive import OneDriveConnector, OneDriveConnectorConfig
from .outlook import OutlookConnector, OutlookConnectorConfig
from .sharepoint import SharePointConnector, SharePointConnectorConfig
from .slack import SlackConnector, SlackConnectorConfig
from .teams import TeamsConnector, TeamsConnectorConfig

__all__ = [
    "CrawlResultConnector",
    "FileUploadConnector",
    "GitConnector",
    "GitConnectorConfig",
    "GmailConnector",
    "GmailConnectorConfig",
    "GoogleDriveConnector",
    "GoogleDriveConnectorConfig",
    "GoogleSheetsConnector",
    "GoogleSheetsConnectorConfig",
    "JiraConnector",
    "JiraConnectorConfig",
    "NotionConnector",
    "NotionConnectorConfig",
    "OneDriveConnector",
    "OneDriveConnectorConfig",
    "OutlookConnector",
    "OutlookConnectorConfig",
    "SharePointConnector",
    "SharePointConnectorConfig",
    "SlackConnector",
    "SlackConnectorConfig",
    "TeamsConnector",
    "TeamsConnectorConfig",
]
