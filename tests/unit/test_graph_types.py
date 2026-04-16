"""Unit tests for GraphRepository Protocol compliance."""

from src.stores.neo4j.types import GraphRepository
from src.stores.neo4j.repository import NoOpNeo4jGraphRepository


class TestGraphRepositoryProtocol:
    def test_noop_satisfies_protocol(self) -> None:
        noop = NoOpNeo4jGraphRepository()
        assert isinstance(noop, GraphRepository)

    def test_arbitrary_class_does_not_satisfy(self) -> None:
        class NotGraph:
            pass
        assert not isinstance(NotGraph(), GraphRepository)
