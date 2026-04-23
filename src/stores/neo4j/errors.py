"""Shared exception tuples for Neo4j read paths.

### 배경

과거 read path 22개 함수가 아래 tuple 을 복사-붙여넣기로 사용했다:

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError)

이 tuple 은 ``neo4j.exceptions.Neo4jError`` 계열 (CypherSyntaxError,
ClientError, ConstraintError, TransientError, ServiceUnavailable 등) 을
포함하지 않아, 쿼리 문법 오류 / 일시 장애 때 500 으로 escalate 하는 갭이
있었다. 2026-04-23 ``search_section_titles`` scope 버그가 이 갭에 의해
답변 실패까지 번진 사례.

### 사용

Read path 에서 실패를 ``[]`` / ``{}`` / ``0`` 로 degrade 하고 싶을 때::

    from src.stores.neo4j.errors import NEO4J_READ_FAILURE

    try:
        return await self._client.execute_query(cypher, params)
    except NEO4J_READ_FAILURE as e:
        logger.warning("Neo4j foo failed: %s", e)
        return []

### Write path 는 이 상수 쓰지 말 것

Write (load_nodes_batch, load_edges_batch 등) 에 Neo4jError 를 조용히 catch
하면 데이터 누락 신호가 사라진다. Write path 는 per-call 에서 log + 재시도 +
counter 업데이트 + caller 에게 실패 개수 반환 패턴을 유지해야 한다.
(현재 ``src/pipelines/neo4j_loader.py`` 가 이 패턴 — tuple 만 Neo4jError
포함으로 확장하되 "조용히 [] 반환" 으로 바꾸지 않음.)

### Exception 을 쓰지 않는 이유

``Exception`` 은 ``AssertionError`` / ``ZeroDivisionError`` / ``TypeError``
로직 버그까지 삼켜 디버깅 어렵게 만든다. Neo4jError 명시적 추가가 올바른
범위.
"""

from __future__ import annotations

from neo4j.exceptions import Neo4jError

# Read path fallback — ``[]`` / ``{}`` 반환용.
NEO4J_READ_FAILURE = (
    Neo4jError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
)

__all__ = ["NEO4J_READ_FAILURE"]
