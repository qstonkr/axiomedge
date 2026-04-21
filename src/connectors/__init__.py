"""Knowledge connectors for local ingestion."""

from .asana import AsanaConnector, AsanaConnectorConfig
from .box import BoxConnector, BoxConnectorConfig
from .crawl_result import CrawlResultConnector
from .dropbox import DropboxConnector, DropboxConnectorConfig
from .file_upload import FileUploadConnector
from .git import GitConnector, GitConnectorConfig
from .github_issues import GitHubIssuesConnector, GitHubIssuesConnectorConfig
from .gmail import GmailConnector, GmailConnectorConfig
from .google_drive import GoogleDriveConnector, GoogleDriveConnectorConfig
from .google_sheets import GoogleSheetsConnector, GoogleSheetsConnectorConfig
from .jira import JiraConnector, JiraConnectorConfig
from .linear import LinearConnector, LinearConnectorConfig
from .notion import NotionConnector, NotionConnectorConfig
from .onedrive import OneDriveConnector, OneDriveConnectorConfig
from .outlook import OutlookConnector, OutlookConnectorConfig
from .salesforce import SalesforceConnector, SalesforceConnectorConfig
from .sharepoint import SharePointConnector, SharePointConnectorConfig
from .slack import SlackConnector, SlackConnectorConfig
from .teams import TeamsConnector, TeamsConnectorConfig

__all__ = [
    "AsanaConnector",
    "AsanaConnectorConfig",
    "BoxConnector",
    "BoxConnectorConfig",
    "CrawlResultConnector",
    "DropboxConnector",
    "DropboxConnectorConfig",
    "FileUploadConnector",
    "GitConnector",
    "GitConnectorConfig",
    "GitHubIssuesConnector",
    "GitHubIssuesConnectorConfig",
    "GmailConnector",
    "GmailConnectorConfig",
    "GoogleDriveConnector",
    "GoogleDriveConnectorConfig",
    "GoogleSheetsConnector",
    "GoogleSheetsConnectorConfig",
    "JiraConnector",
    "JiraConnectorConfig",
    "LinearConnector",
    "LinearConnectorConfig",
    "NotionConnector",
    "NotionConnectorConfig",
    "OneDriveConnector",
    "OneDriveConnectorConfig",
    "OutlookConnector",
    "OutlookConnectorConfig",
    "SalesforceConnector",
    "SalesforceConnectorConfig",
    "SharePointConnector",
    "SharePointConnectorConfig",
    "SlackConnector",
    "SlackConnectorConfig",
    "TeamsConnector",
    "TeamsConnectorConfig",
]
