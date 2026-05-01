import asyncio
import os
import socket
import uuid as _uuid

import httpx
import pytest

API_URL = os.getenv("TEST_API_URL", "http://localhost:8000")
PG_HOST = os.getenv("TEST_PG_HOST", "localhost")
PG_PORT = int(os.getenv("TEST_PG_PORT", "5432"))
PG_DB = os.getenv("TEST_PG_DB", "knowledge_db")
PG_USER = os.getenv("TEST_PG_USER", "knowledge")
PG_PASSWORD = os.getenv("TEST_PG_PASSWORD", "knowledge")
PG_URL = (
    f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
)


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """Quick TCP probe — returns False on connection refused / timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


@pytest.fixture(scope="session")
def api_available() -> bool:
    """Session-cached check: is the API server reachable at TEST_API_URL?

    CI 는 보통 외부 서비스(Ollama/embedding 등) 의존으로 API 를 띄울 수 없으므로
    api fixture 사용 테스트는 자동 skip. 로컬에서 ``make api`` 띄운 상태면 동작.
    """
    from urllib.parse import urlparse
    parsed = urlparse(API_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _is_port_open(host, port)


@pytest.fixture
def api_url():
    return API_URL


@pytest.fixture
def api(api_available):
    """HTTP client for live API. Auto-skips when API server isn't reachable
    (e.g. CI without a running uvicorn). Local dev: ``make api`` 후 그대로 동작.
    """
    if not api_available:
        pytest.skip(
            f"API server not reachable at {API_URL} — start it with "
            "`make api` or set TEST_API_URL.",
        )
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        yield client


# =============================================================================
# P1-3 — Real PostgreSQL integration fixtures (auto-skip when unavailable)
# =============================================================================


@pytest.fixture(scope="session")
def pg_available() -> bool:
    """Session-cached check: is PostgreSQL reachable on configured host:port?"""
    return _is_port_open(PG_HOST, PG_PORT)


@pytest.fixture
def require_postgres(pg_available):  # noqa: ARG001
    """Skip the test when Postgres isn't reachable.

    Use together with the ``@pytest.mark.requires_postgres`` marker for
    clarity. Tests that need a real DB connection should depend on this
    fixture rather than mocking ``async_sessionmaker``.
    """
    if not pg_available:
        pytest.skip(
            f"PostgreSQL not reachable at {PG_HOST}:{PG_PORT} — "
            "skipping integration test (start docker compose to enable)",
        )


@pytest.fixture
async def pg_session_maker(require_postgres):  # noqa: ARG001
    """Real async_sessionmaker for integration tests."""
    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(PG_URL, echo=False, pool_pre_ping=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def seed_ingestion_run(pg_session_maker):
    """Insert a transient ``knowledge_ingestion_runs`` row + cleanup.

    Returns ``(run_id, kb_id)``. Yield -> test runs -> teardown deletes the
    row (CASCADE) so failure rows tied via FK are also removed.
    """
    from datetime import datetime, timezone

    from src.stores.postgres.models import IngestionRunModel

    run_id = str(_uuid.uuid4())
    kb_id = f"_test_kb_{run_id[:8]}"

    async with pg_session_maker() as session:
        session.add(IngestionRunModel(
            id=run_id, kb_id=kb_id,
            source_type="test", source_name="integration",
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    yield run_id, kb_id

    async with pg_session_maker() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(IngestionRunModel).where(IngestionRunModel.id == run_id)
        )
        await session.commit()


@pytest.fixture(scope="session")
def event_loop():
    """Re-use a single asyncio loop per session for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
