"""Route auto-discovery — scan routes/ for modules with `router` attribute.

Replaces the 24-line manual ``app.include_router()`` block in ``app.py``.
Each route module that exposes a ``router`` (or ``admin_router``, ``knowledge_router``)
attribute is automatically registered.

Usage in app.py::

    from src.api.route_discovery import discover_and_register_routes
    discover_and_register_routes(app)

Convention:
    - ``router`` — primary router (always registered)
    - ``admin_router`` — admin-prefixed router (registered if present)
    - ``knowledge_router`` — knowledge-prefixed router (registered if present)
    - ``rag_query_router`` — RAG query router (registered if present)
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Router attribute names to auto-detect on each route module
_ROUTER_ATTRS = ("router", "admin_router", "knowledge_router", "rag_query_router")


def discover_and_register_routes(app: FastAPI) -> int:
    """Scan ``src.api.routes`` package and register all discovered routers.

    Returns:
        Number of routers registered.
    """
    import src.api.routes as routes_pkg

    registered = 0
    for module_info in pkgutil.iter_modules(routes_pkg.__path__):
        module_name = f"src.api.routes.{module_info.name}"
        try:
            module = importlib.import_module(module_name)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.warning("Failed to import route module %s: %s", module_name, e)
            continue

        for attr_name in _ROUTER_ATTRS:
            router = getattr(module, attr_name, None)
            if router is not None and hasattr(router, "routes"):
                app.include_router(router)
                registered += 1
                logger.debug(
                    "Auto-registered %s.%s (%d routes)",
                    module_info.name, attr_name, len(router.routes),
                )

    logger.info("Route discovery: %d routers registered", registered)
    return registered
