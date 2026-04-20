#!/usr/bin/env python3
"""Dump FastAPI's OpenAPI spec to stdout (no uvicorn needed).

Used by ``apps/web`` to regenerate TypeScript types when the API surface
changes. Bypasses a couple of startup-only imports (tracing) so it works
in environments where opentelemetry isn't installed.

Usage:
    uv run python scripts/dump_openapi.py > apps/web/openapi.json
    pnpm --dir apps/web exec openapi-typescript apps/web/openapi.json \\
        -o apps/web/src/lib/api/types.ts
"""

from __future__ import annotations

import json
import logging
import sys
import types

# Suppress all logging (incl. route_discovery JSON line) BEFORE importing
# src.api.app — otherwise structured-log lines pollute stdout and the
# downstream openapi-typescript parser sees "two YAML documents" and dies.
logging.basicConfig(
    level=logging.CRITICAL + 1,
    handlers=[logging.StreamHandler(sys.stderr)],
    force=True,
)

# Stub `src.core.observability.tracing` before src.api.app imports it.
# Tracing only matters at runtime; for spec generation it's noise.
_tracing_stub = types.ModuleType("src.core.observability.tracing")


def _noop(*_args: object, **_kwargs: object) -> None:
    return None


class _NoopCtx:
    def __enter__(self) -> "_NoopCtx":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


_tracing_stub.init_tracing = _noop  # type: ignore[attr-defined]
_tracing_stub.trace_rag_stage = lambda _name: _NoopCtx()  # type: ignore[attr-defined]
sys.modules.setdefault("src.core.observability.tracing", _tracing_stub)


def main() -> int:
    from src.api.app import app

    json.dump(app.openapi(), sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
