"""Git repository knowledge connector.

Clones/pulls a git repository, walks files with glob filters, and emits
RawDocuments for the ingestion pipeline. Uses the local `git` CLI via
subprocess so any git host (GitHub, GitLab, Bitbucket, internal) is supported
through SSH or HTTPS auth.
"""

from .client import GitClient, GitCommandError
from .config import GitConnectorConfig
from .connector import GitConnector

__all__ = [
    "GitClient",
    "GitCommandError",
    "GitConnector",
    "GitConnectorConfig",
]
