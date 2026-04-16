"""Neo4j Client

Neo4j 연결 및 트랜잭션 관리.
설계서 Line 6000+ 참조.

Created: 2026-02-04 (Sprint 9)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_BOLT_URI = "bolt://localhost:7687"


class Neo4jClient:
    """Neo4j 연결 및 트랜잭션 관리

    Neo4j AsyncGraphDatabase driver를 래핑하여 연결 관리와
    트랜잭션 처리를 단순화합니다.
    """

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str = "neo4j",
        *,
        auth_disabled: bool | None = None,
    ) -> None:
        """Initialize Neo4j client.

        Args:
            uri: Neo4j URI (bolt://localhost:7687)
            user: 사용자명
            password: 비밀번호
            database: 데이터베이스명 (기본: neo4j)
            auth_disabled: True이면 인증 없이 연결 (NEO4J_AUTH=none)
        """
        self.uri = uri or os.getenv("NEO4J_URI", _DEFAULT_BOLT_URI)
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password if password is not None else os.getenv("NEO4J_PASSWORD", "")
        self.database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        if auth_disabled is None:
            neo4j_auth = os.getenv("NEO4J_AUTH", "")
            auth_disabled = (
                neo4j_auth.lower() == "none"
                or (not self.password and self.uri != _DEFAULT_BOLT_URI)
            )
        self._auth_disabled = auth_disabled
        self._driver = None

    async def connect(self) -> None:
        """Neo4j 연결"""
        try:
            from neo4j import AsyncGraphDatabase

            auth = None if self._auth_disabled else (self.user, self.password)
            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=auth,
            )
            # 연결 테스트
            async with self._driver.session(database=self.database) as session:
                result = await session.run("RETURN 1 as n")
                await result.consume()
            logger.info(f"Connected to Neo4j at {self.uri}")
        except ImportError:
            logger.warning("neo4j package not installed. Using NoOp client.")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    async def close(self) -> None:
        """Neo4j 연결 종료"""
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        """세션 컨텍스트 매니저"""
        if not self._driver:
            await self.connect()
        async with self._driver.session(database=self.database) as session:
            yield session

    async def execute_query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Cypher 쿼리 실행

        Args:
            cypher: Cypher 쿼리
            params: 쿼리 파라미터

        Returns:
            쿼리 결과 리스트
        """
        async with self.session() as session:
            result = await session.run(cypher, params or {})
            records = [record.data() async for record in result]
            return records

    async def execute_write(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """쓰기 트랜잭션 실행

        Args:
            cypher: Cypher 쿼리
            params: 쿼리 파라미터

        Returns:
            트랜잭션 결과 요약
        """
        async with self.session() as session:
            result = await session.run(cypher, params or {})
            summary = await result.consume()
            return {
                "nodes_created": summary.counters.nodes_created,
                "nodes_deleted": summary.counters.nodes_deleted,
                "relationships_created": summary.counters.relationships_created,
                "relationships_deleted": summary.counters.relationships_deleted,
                "properties_set": summary.counters.properties_set,
            }

    async def execute_batch(
        self,
        queries: list[tuple[str, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        """여러 쿼리 배치 실행

        Args:
            queries: (cypher, params) 튜플 리스트

        Returns:
            각 쿼리의 결과 요약 리스트
        """
        results = []
        async with self.session() as session:
            tx = await session.begin_transaction()
            try:
                for cypher, params in queries:
                    result = await tx.run(cypher, params or {})
                    summary = await result.consume()
                    results.append({
                        "nodes_created": summary.counters.nodes_created,
                        "relationships_created": summary.counters.relationships_created,
                    })
                await tx.commit()
            except Exception:
                await tx.rollback()
                raise
        return results

    async def execute_unwind_batch(
        self,
        cypher: str,
        *,
        param_name: str,
        items: list[dict[str, Any]],
        batch_size: int = 5000,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute UNWIND write query in chunks.

        Args:
            cypher: Cypher query using `UNWIND $<param_name> AS ...`
            param_name: Name of list parameter bound to each chunk
            items: Items to execute
            batch_size: Chunk size per transaction (default 5000)
            extra_params: Additional Cypher parameters (e.g. $kb_id)

        Returns:
            List of transaction summaries per chunk.
        """
        summaries: list[dict[str, Any]] = []
        if not items:
            return summaries

        effective_batch = max(1, int(batch_size))
        async with self.session() as session:
            for idx in range(0, len(items), effective_batch):
                chunk = items[idx : idx + effective_batch]
                params: dict[str, Any] = {param_name: chunk}
                if extra_params:
                    params.update(extra_params)
                result = await session.run(cypher, params)
                summary = await result.consume()
                summaries.append(
                    {
                        "batch_index": (idx // effective_batch) + 1,
                        "batch_size": len(chunk),
                        "nodes_created": summary.counters.nodes_created,
                        "relationships_created": summary.counters.relationships_created,
                        "properties_set": summary.counters.properties_set,
                    }
                )
        return summaries

    async def health_check(self) -> bool:
        """연결 상태 확인"""
        try:
            async with self.session() as session:
                result = await session.run("RETURN 1 as n")
                await result.consume()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Neo4j health check failed: {e}")
            return False


class NoOpNeo4jClient:
    """NoOp Neo4j Client (테스트/개발용)"""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str = "neo4j",
    ) -> None:
        self.uri = uri or _DEFAULT_BOLT_URI
        self.user = user or "neo4j"
        self.database = database

    async def connect(self) -> None:
        await asyncio.sleep(0)
        logger.debug("[NoOp] Neo4j connect called")

    async def close(self) -> None:
        await asyncio.sleep(0)
        logger.debug("[NoOp] Neo4j close called")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        await asyncio.sleep(0)
        yield NoOpSession()

    async def execute_query(
        self,
        cypher: str,
        _params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        logger.debug("[NoOp] execute_query: %s...", cypher[:100])
        return []

    async def execute_write(
        self,
        cypher: str,
        _params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        logger.debug("[NoOp] execute_write: %s...", cypher[:100])
        return {
            "nodes_created": 0,
            "nodes_deleted": 0,
            "relationships_created": 0,
            "relationships_deleted": 0,
            "properties_set": 0,
        }

    async def execute_batch(
        self,
        queries: list[tuple[str, dict[str, Any] | None]],
    ) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        logger.debug("[NoOp] execute_batch: %d queries", len(queries))
        return [{"nodes_created": 0, "relationships_created": 0}] * len(queries)

    async def execute_unwind_batch(
        self,
        _cypher: str,
        *,
        param_name: str,
        items: list[dict[str, Any]],
        batch_size: int = 5000,
    ) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        logger.debug(
            "[NoOp] execute_unwind_batch: param=%s, items=%d, batch_size=%d",
            param_name,
            len(items),
            batch_size,
        )
        return [
            {
                "batch_index": 1,
                "batch_size": len(items),
                "nodes_created": 0,
                "relationships_created": 0,
                "properties_set": 0,
            }
        ] if items else []

    async def health_check(self) -> bool:
        await asyncio.sleep(0)
        return True


class NoOpSession:
    """NoOp Session (테스트용)"""

    async def run(self, _cypher: str, _params: dict | None = None) -> "NoOpResult":
        await asyncio.sleep(0)
        return NoOpResult()

    async def begin_transaction(self) -> "NoOpTransaction":
        await asyncio.sleep(0)
        return NoOpTransaction()


class NoOpResult:
    """NoOp Result (테스트용)"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def consume(self) -> "NoOpSummary":
        await asyncio.sleep(0)
        return NoOpSummary()


class NoOpSummary:
    """NoOp Summary (테스트용)"""

    class Counters:
        nodes_created = 0
        nodes_deleted = 0
        relationships_created = 0
        relationships_deleted = 0
        properties_set = 0

    counters = Counters()


class NoOpTransaction:
    """NoOp Transaction (테스트용)"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await asyncio.sleep(0)

    async def run(self, _cypher: str, _params: dict | None = None) -> NoOpResult:
        await asyncio.sleep(0)
        return NoOpResult()

    async def commit(self) -> None:
        await asyncio.sleep(0)
