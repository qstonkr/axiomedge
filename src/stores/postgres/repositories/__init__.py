"""Knowledge Local - Database Repositories.

All repository classes for PostgreSQL persistence.
"""

from src.stores.postgres.repositories.kb_registry import KBRegistryRepository
from src.stores.postgres.repositories.glossary import GlossaryRepository
from src.stores.postgres.repositories.ownership import (
    DocumentOwnerRepository,
    TopicOwnerRepository,
    ErrorReportRepository,
)
from src.stores.postgres.repositories.feedback import FeedbackRepository
from src.stores.postgres.repositories.ingestion_run import IngestionRunRepository
from src.stores.postgres.repositories.trust_score import TrustScoreRepository
from src.stores.postgres.repositories.lifecycle import DocumentLifecycleRepository
from src.stores.postgres.repositories.data_source import DataSourceRepository
from src.stores.postgres.repositories.traceability import ProvenanceRepository
from src.stores.postgres.repositories.category import CategoryRepository

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
