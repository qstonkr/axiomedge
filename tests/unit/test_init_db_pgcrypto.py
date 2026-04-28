import inspect
import src.stores.postgres.init_db as init_db


def test_init_database_creates_pgcrypto_extension():
    """init_database must run CREATE EXTENSION IF NOT EXISTS pgcrypto."""
    src = inspect.getsource(init_db.init_database)
    assert "pgcrypto" in src.lower()
    assert "create extension" in src.lower()
