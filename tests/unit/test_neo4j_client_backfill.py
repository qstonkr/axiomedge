"""Backfill coverage for src/stores/neo4j/client.py.

Covers connect(), session(), execute_batch(), execute_unwind_batch(),
health_check() success, and auth_disabled inference paths that are
NOT tested in test_graph_full.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.stores.neo4j.client import (
    Neo4jClient,
    NoOpNeo4jClient,
    NoOpResult,
    NoOpSession,
    NoOpSummary,
    NoOpTransaction,
)


# -----------------------------------------------------------------------
# Neo4jClient — __init__ auth_disabled inference
# -----------------------------------------------------------------------

class TestNeo4jClientAuthDisabledInference:
    """auth_disabled auto-detection edge cases."""

    def test_auth_disabled_no_password_non_default_uri(self):
        """Empty password + non-default URI -> auth_disabled."""
        client = Neo4jClient(
            uri="bolt://remote:7687", password=""
        )
        assert client._auth_disabled is True

    def test_auth_disabled_false_default_uri_no_password(self):
        """Default URI + empty password -> NOT disabled (env NEO4J_AUTH '')."""
        with patch.dict(
            "os.environ", {"NEO4J_AUTH": "", "NEO4J_PASSWORD": ""}, clear=False
        ):
            client = Neo4jClient(uri="bolt://localhost:7687", password="")
            assert client._auth_disabled is False

    def test_auth_disabled_explicit_true(self):
        client = Neo4jClient(auth_disabled=True)
        assert client._auth_disabled is True

    def test_auth_disabled_explicit_false(self):
        client = Neo4jClient(auth_disabled=False)
        assert client._auth_disabled is False


# -----------------------------------------------------------------------
# Neo4jClient — connect()
# -----------------------------------------------------------------------

class TestNeo4jClientConnect:
    async def test_connect_success(self):
        mock_driver = MagicMock()
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.consume = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_driver.session.return_value = mock_session

        with patch(
            "src.stores.neo4j.client.AsyncGraphDatabase",
            create=True,
        ):
            # Patch the import inside connect()
            import types
            fake_neo4j = types.ModuleType("neo4j")
            fake_neo4j.AsyncGraphDatabase = MagicMock()
            fake_neo4j.AsyncGraphDatabase.driver.return_value = mock_driver

            with patch.dict("sys.modules", {"neo4j": fake_neo4j}):
                client = Neo4jClient(
                    uri="bolt://localhost:7687",
                    user="neo4j",
                    password="pass",
                )
                await client.connect()
                assert client._driver is mock_driver

    async def test_connect_import_error(self):
        client = Neo4jClient()
        with patch.dict("sys.modules", {"neo4j": None}):
            with pytest.raises(ImportError):
                await client.connect()

    async def test_connect_general_exception(self):
        import types
        fake_neo4j = types.ModuleType("neo4j")
        fake_neo4j.AsyncGraphDatabase = MagicMock()
        fake_neo4j.AsyncGraphDatabase.driver.side_effect = RuntimeError(
            "connection refused"
        )

        client = Neo4jClient()
        with patch.dict("sys.modules", {"neo4j": fake_neo4j}):
            with pytest.raises(RuntimeError, match="connection refused"):
                await client.connect()

    async def test_connect_auth_disabled_passes_none_auth(self):
        mock_driver = MagicMock()
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.consume = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_driver.session.return_value = mock_session

        import types
        fake_neo4j = types.ModuleType("neo4j")
        fake_neo4j.AsyncGraphDatabase = MagicMock()
        fake_neo4j.AsyncGraphDatabase.driver.return_value = mock_driver

        client = Neo4jClient(auth_disabled=True)
        with patch.dict("sys.modules", {"neo4j": fake_neo4j}):
            await client.connect()
            call_kwargs = (
                fake_neo4j.AsyncGraphDatabase.driver.call_args
            )
            assert call_kwargs[1]["auth"] is None


# -----------------------------------------------------------------------
# Neo4jClient — session() context manager
# -----------------------------------------------------------------------

class TestNeo4jClientSession:
    async def test_session_auto_connects(self):
        """session() should call connect() when _driver is None."""
        client = Neo4jClient()
        client._driver = None

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session

        async def fake_connect():
            client._driver = mock_driver

        with patch.object(client, "connect", side_effect=fake_connect):
            async with client.session() as sess:
                assert sess is mock_session

    async def test_session_reuses_driver(self):
        """session() should NOT call connect() when _driver exists."""
        client = Neo4jClient()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        with patch.object(client, "connect") as mock_connect:
            async with client.session() as sess:
                assert sess is mock_session
            mock_connect.assert_not_called()


# -----------------------------------------------------------------------
# Neo4jClient — execute_batch()
# -----------------------------------------------------------------------

class TestNeo4jClientExecuteBatch:
    async def test_execute_batch_success(self):
        client = Neo4jClient()

        mock_summary1 = MagicMock()
        mock_summary1.counters.nodes_created = 2
        mock_summary1.counters.relationships_created = 1

        mock_summary2 = MagicMock()
        mock_summary2.counters.nodes_created = 0
        mock_summary2.counters.relationships_created = 3

        mock_result1 = AsyncMock()
        mock_result1.consume = AsyncMock(return_value=mock_summary1)
        mock_result2 = AsyncMock()
        mock_result2.consume = AsyncMock(return_value=mock_summary2)

        mock_tx = AsyncMock()
        mock_tx.run = AsyncMock(
            side_effect=[mock_result1, mock_result2]
        )
        mock_tx.commit = AsyncMock()
        mock_tx.rollback = AsyncMock()

        mock_session = AsyncMock()
        mock_session.begin_transaction = AsyncMock(return_value=mock_tx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        queries = [
            ("CREATE (n:A)", {"name": "a"}),
            ("CREATE (n:B)", None),
        ]
        results = await client.execute_batch(queries)

        assert len(results) == 2
        assert results[0]["nodes_created"] == 2
        assert results[1]["relationships_created"] == 3
        mock_tx.commit.assert_awaited_once()
        mock_tx.rollback.assert_not_awaited()

    async def test_execute_batch_rollback_on_error(self):
        client = Neo4jClient()

        mock_tx = AsyncMock()
        mock_tx.run = AsyncMock(
            side_effect=RuntimeError("query failed")
        )
        mock_tx.commit = AsyncMock()
        mock_tx.rollback = AsyncMock()

        mock_session = AsyncMock()
        mock_session.begin_transaction = AsyncMock(return_value=mock_tx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        with pytest.raises(RuntimeError, match="query failed"):
            await client.execute_batch([("BAD QUERY", None)])

        mock_tx.rollback.assert_awaited_once()
        mock_tx.commit.assert_not_awaited()

    async def test_execute_batch_empty(self):
        client = Neo4jClient()
        mock_tx = AsyncMock()
        mock_tx.commit = AsyncMock()
        mock_tx.rollback = AsyncMock()

        mock_session = AsyncMock()
        mock_session.begin_transaction = AsyncMock(return_value=mock_tx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        results = await client.execute_batch([])
        assert results == []
        mock_tx.commit.assert_awaited_once()


# -----------------------------------------------------------------------
# Neo4jClient — execute_unwind_batch()
# -----------------------------------------------------------------------

class TestNeo4jClientExecuteUnwindBatch:
    async def test_empty_items_returns_empty(self):
        client = Neo4jClient()
        client._driver = MagicMock()
        result = await client.execute_unwind_batch(
            "UNWIND $rows AS r MERGE (n:T {id: r.id})",
            param_name="rows",
            items=[],
        )
        assert result == []

    async def test_single_batch(self):
        client = Neo4jClient()
        mock_summary = MagicMock()
        mock_summary.counters.nodes_created = 3
        mock_summary.counters.relationships_created = 0
        mock_summary.counters.properties_set = 6

        mock_result = AsyncMock()
        mock_result.consume = AsyncMock(return_value=mock_summary)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        items = [{"id": i} for i in range(3)]
        result = await client.execute_unwind_batch(
            "UNWIND $rows AS r MERGE (n:T {id: r.id})",
            param_name="rows",
            items=items,
            batch_size=100,
        )
        assert len(result) == 1
        assert result[0]["batch_index"] == 1
        assert result[0]["batch_size"] == 3
        assert result[0]["nodes_created"] == 3

    async def test_multiple_chunks(self):
        client = Neo4jClient()

        def make_summary(n_created):
            s = MagicMock()
            s.counters.nodes_created = n_created
            s.counters.relationships_created = 0
            s.counters.properties_set = n_created
            return s

        mock_result1 = AsyncMock()
        mock_result1.consume = AsyncMock(
            return_value=make_summary(2)
        )
        mock_result2 = AsyncMock()
        mock_result2.consume = AsyncMock(
            return_value=make_summary(1)
        )

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(
            side_effect=[mock_result1, mock_result2]
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        items = [{"id": i} for i in range(3)]
        result = await client.execute_unwind_batch(
            "UNWIND $rows AS r MERGE (n:T {id: r.id})",
            param_name="rows",
            items=items,
            batch_size=2,
        )
        assert len(result) == 2
        assert result[0]["batch_index"] == 1
        assert result[0]["batch_size"] == 2
        assert result[1]["batch_index"] == 2
        assert result[1]["batch_size"] == 1

    async def test_extra_params_forwarded(self):
        client = Neo4jClient()
        mock_summary = MagicMock()
        mock_summary.counters.nodes_created = 1
        mock_summary.counters.relationships_created = 0
        mock_summary.counters.properties_set = 1

        mock_result = AsyncMock()
        mock_result.consume = AsyncMock(return_value=mock_summary)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        await client.execute_unwind_batch(
            "UNWIND $items AS i MERGE (n:T {id: i.id, kb: $kb_id})",
            param_name="items",
            items=[{"id": 1}],
            extra_params={"kb_id": "kb-001"},
        )
        call_args = mock_session.run.call_args
        assert call_args[0][1]["kb_id"] == "kb-001"
        assert call_args[0][1]["items"] == [{"id": 1}]


# -----------------------------------------------------------------------
# Neo4jClient — health_check() success
# -----------------------------------------------------------------------

class TestNeo4jClientHealthCheck:
    async def test_health_check_success(self):
        client = Neo4jClient()
        mock_result = AsyncMock()
        mock_result.consume = AsyncMock()

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        assert await client.health_check() is True

    async def test_health_check_exception(self):
        client = Neo4jClient()
        mock_session = AsyncMock()
        mock_session.run = AsyncMock(
            side_effect=RuntimeError("timeout")
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        client._driver = mock_driver

        assert await client.health_check() is False


# -----------------------------------------------------------------------
# Neo4jClient — close()
# -----------------------------------------------------------------------

class TestNeo4jClientClose:
    async def test_close_with_driver_resets_to_none(self):
        client = Neo4jClient()
        mock_driver = AsyncMock()
        client._driver = mock_driver
        await client.close()
        assert client._driver is None
        mock_driver.close.assert_awaited_once()

    async def test_close_without_driver_noop(self):
        client = Neo4jClient()
        client._driver = None
        await client.close()
        assert client._driver is None


# -----------------------------------------------------------------------
# NoOp classes — auxiliary coverage
# -----------------------------------------------------------------------

class TestNoOpSessionBackfill:
    async def test_run(self):
        session = NoOpSession()
        result = await session.run("RETURN 1")
        assert isinstance(result, NoOpResult)

    async def test_begin_transaction(self):
        session = NoOpSession()
        tx = await session.begin_transaction()
        assert isinstance(tx, NoOpTransaction)


class TestNoOpResultBackfill:
    async def test_aiter_stops_immediately(self):
        result = NoOpResult()
        items = [item async for item in result]
        assert items == []

    async def test_consume(self):
        result = NoOpResult()
        summary = await result.consume()
        assert isinstance(summary, NoOpSummary)


class TestNoOpSummaryBackfill:
    def test_counters(self):
        s = NoOpSummary()
        assert s.counters.nodes_created == 0
        assert s.counters.nodes_deleted == 0
        assert s.counters.relationships_created == 0
        assert s.counters.relationships_deleted == 0
        assert s.counters.properties_set == 0


class TestNoOpTransactionBackfill:
    async def test_aenter_aexit(self):
        tx = NoOpTransaction()
        async with tx as t:
            assert t is tx

    async def test_run(self):
        tx = NoOpTransaction()
        result = await tx.run("RETURN 1")
        assert isinstance(result, NoOpResult)

    async def test_commit(self):
        tx = NoOpTransaction()
        await tx.commit()  # should not raise


class TestNoOpNeo4jClientUnwindBatch:
    async def test_nonempty_items(self):
        client = NoOpNeo4jClient()
        result = await client.execute_unwind_batch(
            "Q", param_name="items", items=[{"a": 1}, {"b": 2}]
        )
        assert len(result) == 1
        assert result[0]["batch_size"] == 2

    async def test_empty_items(self):
        client = NoOpNeo4jClient()
        result = await client.execute_unwind_batch(
            "Q", param_name="items", items=[]
        )
        assert result == []
