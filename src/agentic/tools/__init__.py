"""Tool registry — agent planner 가 호출 가능한 도구 목록 SSOT.

새 도구 추가 절차:
  1. ``Tool`` 상속 + ``execute`` 구현
  2. ``DEFAULT_TOOLS`` 에 클래스 추가
  3. 단위 테스트 작성 (mock state)
"""

from __future__ import annotations

from src.agentic.protocols import Tool, ToolSpec
from src.agentic.tools.glossary import GlossaryLookupTool
from src.agentic.tools.graph_query import GraphQueryTool
from src.agentic.tools.kb_lister import KBListerTool
from src.agentic.tools.qdrant_search import QdrantSearchTool
from src.agentic.tools.re_ocr import ReOcrTool
from src.agentic.tools.time_resolver import TimeResolverTool


class ToolRegistry:
    """Name → Tool instance lookup.

    Agent loop 가 ``registry.get(step.tool).execute(...)`` 패턴으로 호출.
    Planner 는 ``registry.specs()`` 로 LLM prompt 의 도구 카탈로그를 받음.
    """

    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Duplicate tool names registered")

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}. Registered: {sorted(self._tools)}")
        return self._tools[name]

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools


DEFAULT_TOOLS: list[type[Tool]] = [
    QdrantSearchTool,
    GraphQueryTool,
    GlossaryLookupTool,
    TimeResolverTool,
    KBListerTool,
    ReOcrTool,
]


def build_default_registry() -> ToolRegistry:
    """기본 5개 도구로 registry 생성."""
    return ToolRegistry([cls() for cls in DEFAULT_TOOLS])


__all__ = [
    "DEFAULT_TOOLS",
    "GlossaryLookupTool",
    "GraphQueryTool",
    "KBListerTool",
    "QdrantSearchTool",
    "ReOcrTool",
    "TimeResolverTool",
    "ToolRegistry",
    "build_default_registry",
]
