"""Tree Index Builder — heading_path 기반 Neo4j 트리 구축.

기존 chunker가 추출한 heading_path 문자열을 파싱하여
Neo4j에 TreeRoot → TreeSection → TreePage 계층 구조를 생성한다.

LLM 호출 없음 — heading_path 메타데이터만 활용.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _path_hash(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _parent_path(full_path: str) -> str:
    """full_path에서 마지막 세그먼트를 제거한 부모 경로 반환."""
    return " > ".join(full_path.split(" > ")[:-1])


def parse_heading_path(heading_path: str) -> list[dict[str, Any]]:
    """heading_path 문자열을 계층 노드 리스트로 변환.

    >>> parse_heading_path("설치 가이드 > 사전 요구사항 > Python 설정")
    [
        {"level": 1, "title": "설치 가이드", "full_path": "설치 가이드", "path_hash": "..."},
        {"level": 2, "title": "사전 요구사항", "full_path": "설치 가이드 > 사전 요구사항", ...},
        {"level": 3, "title": "Python 설정", "full_path": "설치 가이드 > 사전 요구사항 > Python 설정", ...},
    ]
    """
    if not heading_path or not heading_path.strip():
        return []
    parts = [p.strip() for p in heading_path.split(">") if p.strip()]
    if not parts:
        return []
    nodes = []
    for i, title in enumerate(parts):
        full_path = " > ".join(parts[: i + 1])
        nodes.append({
            "level": i + 1,
            "title": title,
            "full_path": full_path,
            "path_hash": _path_hash(full_path),
        })
    return nodes


def build_tree_from_chunks(
    kb_id: str,
    doc_id: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """청크 리스트에서 트리 구조 추출.

    Args:
        kb_id: Knowledge base ID
        doc_id: Document ID
        chunks: 각 청크는 {"chunk_id": str, "heading_path": str, "chunk_index": int} 포함

    Returns:
        {
            "root": {"node_id": str, "doc_id": str, "kb_id": str},
            "sections": [{"node_id", "level", "title", "full_path", "order", "doc_id", "char_count"}],
            "pages": [{"node_id", "chunk_id", "chunk_index", "doc_id", "section_id"}],
            "edges": [{"source", "target", "type"}],
        }
    """
    root_id = f"{kb_id}:{doc_id}"
    sections: dict[str, dict[str, Any]] = {}  # path_hash → section info
    pages: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for chunk in chunks:
        heading_path = chunk.get("heading_path", "") or ""
        chunk_id = chunk.get("chunk_id", "")
        chunk_index = chunk.get("chunk_index", 0)

        parsed = parse_heading_path(heading_path)

        if not parsed:
            flat_hash = _path_hash("__flat__")
            if flat_hash not in sections:
                sections[flat_hash] = {
                    "node_id": f"{kb_id}:{doc_id}:section:{flat_hash}",
                    "level": 1,
                    "title": "(본문)",
                    "full_path": "(본문)",
                    "order": 0,
                    "doc_id": doc_id,
                    "kb_id": kb_id,
                    "char_count": 0,
                }
            leaf_section_id = sections[flat_hash]["node_id"]
        else:
            for node_info in parsed:
                ph = node_info["path_hash"]
                if ph not in sections:
                    sections[ph] = {
                        "node_id": f"{kb_id}:{doc_id}:section:{ph}",
                        "level": node_info["level"],
                        "title": node_info["title"],
                        "full_path": node_info["full_path"],
                        "order": len(sections),
                        "doc_id": doc_id,
                        "kb_id": kb_id,
                        "char_count": 0,
                    }
            leaf_section_id = sections[parsed[-1]["path_hash"]]["node_id"]

        page_id = f"{kb_id}:{doc_id}:page:{chunk_index}"
        pages.append({
            "node_id": page_id,
            "chunk_id": chunk_id,
            "chunk_index": chunk_index,
            "doc_id": doc_id,
            "kb_id": kb_id,
            "section_id": leaf_section_id,
        })

    # 엣지 구축
    section_list = sorted(sections.values(), key=lambda s: s["order"])

    # Root → Level 1 섹션
    for sec in section_list:
        if sec["level"] == 1:
            edges.append({"source": root_id, "target": sec["node_id"], "type": "HAS_TREE_SECTION"})

    # 부모 섹션 → 자식 섹션
    for sec in section_list:
        if sec["level"] > 1:
            parent_hash = _path_hash(_parent_path(sec["full_path"]))
            if parent_hash in sections:
                edges.append({
                    "source": sections[parent_hash]["node_id"],
                    "target": sec["node_id"],
                    "type": "HAS_TREE_SECTION",
                })

    # 섹션 → 페이지
    for page in pages:
        edges.append({
            "source": page["section_id"],
            "target": page["node_id"],
            "type": "HAS_TREE_PAGE",
        })

    # 같은 섹션 내 페이지 형제 관계 (TREE_NEXT_SIBLING)
    section_pages: dict[str, list[dict]] = {}
    for page in pages:
        section_pages.setdefault(page["section_id"], []).append(page)

    for _sec_id, sec_pages in section_pages.items():
        sorted_pages = sorted(sec_pages, key=lambda p: p["chunk_index"])
        for i in range(len(sorted_pages) - 1):
            edges.append({
                "source": sorted_pages[i]["node_id"],
                "target": sorted_pages[i + 1]["node_id"],
                "type": "TREE_NEXT_SIBLING",
            })

    # 같은 레벨 섹션 형제 관계
    by_parent: dict[str, list[dict]] = {}
    for sec in section_list:
        if sec["level"] == 1:
            by_parent.setdefault("root", []).append(sec)
        elif sec["level"] > 1:
            by_parent.setdefault(_path_hash(_parent_path(sec["full_path"])), []).append(sec)

    for siblings in by_parent.values():
        for i in range(len(siblings) - 1):
            edges.append({
                "source": siblings[i]["node_id"],
                "target": siblings[i + 1]["node_id"],
                "type": "TREE_NEXT_SIBLING",
            })

    # Document → TreeRoot
    edges.append({"source": doc_id, "target": root_id, "type": "HAS_TREE_ROOT"})

    return {
        "root": {"node_id": root_id, "doc_id": doc_id, "kb_id": kb_id},
        "sections": section_list,
        "pages": pages,
        "edges": edges,
    }


async def persist_tree_to_neo4j(
    graph_repo: Any,
    tree_data: dict[str, Any],
) -> int:
    """트리 데이터를 Neo4j에 저장.

    Returns:
        생성된 총 노드 수.
    """
    root = tree_data["root"]
    sections = tree_data["sections"]
    pages = tree_data["pages"]
    edges = tree_data["edges"]

    # 1. TreeRoot 노드
    root_nodes = [{
        "node_id": root["node_id"],
        "title": f"TreeRoot:{root['doc_id']}",
        "properties": {"doc_id": root["doc_id"], "kb_id": root["kb_id"]},
    }]
    await graph_repo.batch_upsert_nodes("TreeRoot", root_nodes)

    # 2-3. TreeSection + TreePage 노드 (병렬)
    node_coros = []
    if sections:
        section_nodes = [{
            "node_id": s["node_id"],
            "title": s["title"],
            "properties": {
                "doc_id": s["doc_id"],
                "kb_id": s["kb_id"],
                "level": s["level"],
                "full_path": s["full_path"],
                "order": s["order"],
                "char_count": s.get("char_count", 0),
            },
        } for s in sections]
        node_coros.append(graph_repo.batch_upsert_nodes("TreeSection", section_nodes))

    if pages:
        page_nodes = [{
            "node_id": p["node_id"],
            "title": f"page:{p['chunk_index']}",
            "properties": {
                "doc_id": p["doc_id"],
                "kb_id": p["kb_id"],
                "chunk_id": p["chunk_id"],
                "chunk_index": p["chunk_index"],
            },
        } for p in pages]
        node_coros.append(graph_repo.batch_upsert_nodes("TreePage", page_nodes))

    for coro in node_coros:
        await coro

    # 4. 엣지 (타입별 순차 — Neo4j deadlock 방지)
    edge_by_type: dict[str, list[dict]] = {}
    for e in edges:
        edge_by_type.setdefault(e["type"], []).append(e)

    for rel_type, rel_edges in edge_by_type.items():
        batch = [{"source": e["source"], "target": e["target"], "properties": {}}
                 for e in rel_edges]
        await graph_repo.batch_upsert_edges(rel_type, batch)

    total = 1 + len(sections) + len(pages)
    logger.info(
        "Tree index persisted: doc_id=%s, sections=%d, pages=%d, edges=%d",
        root["doc_id"], len(sections), len(pages), len(edges),
    )
    return total
