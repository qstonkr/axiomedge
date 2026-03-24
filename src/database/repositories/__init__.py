"""Knowledge Local - Database Repositories.

All repository classes for PostgreSQL persistence.
"""

from src.database.repositories.kb_registry import KBRegistryRepository
from src.database.repositories.glossary import GlossaryRepository
from src.database.repositories.ownership import (
    DocumentOwnerRepository,
    TopicOwnerRepository,
    ErrorReportRepository,
)
from src.database.repositories.feedback import FeedbackRepository
from src.database.repositories.ingestion_run import IngestionRunRepository
from src.database.repositories.trust_score import TrustScoreRepository
from src.database.repositories.lifecycle import DocumentLifecycleRepository
from src.database.repositories.data_source import DataSourceRepository
from src.database.repositories.traceability import ProvenanceRepository
from src.database.repositories.category import CategoryRepository

__all__ = [
    "KBRegistryRepository",
    "GlossaryRepository",
    "DocumentOwnerRepository",
    "TopicOwnerRepository",
    "ErrorReportRepository",
    "FeedbackRepository",
    "IngestionRunRepository",
    "TrustScoreRepository",
    "DocumentLifecycleRepository",
    "DataSourceRepository",
    "ProvenanceRepository",
    "CategoryRepository",
]
