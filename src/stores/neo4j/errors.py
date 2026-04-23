"""Shared exception tuple for Neo4j call sites.

### 배경

과거 Neo4j read/write 함수들이 아래 tuple 을 복사-붙여넣기로 사용했다:

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError)

이 tuple 은 ``neo4j.exceptions.Neo4jError`` 계열 (CypherSyntaxError,
ClientError, ConstraintError, TransientError, ServiceUnavailable 등) 을
포함하지 않아, 쿼리 문법 오류 / 일시 장애 때 500 으로 escalate 하는 갭이
있었다. 2026-04-23 ``search_section_titles`` scope 버그가 이 갭에 의해
답변 실패까지 번진 사례.

### 사용

Neo4j 호출을 감쌀 때 일관된 tuple 로 잡고 싶을 때::

    from src.stores.neo4j.errors import NEO4J_FAILURE

    try:
        return await self._client.execute_query(cypher, params)
    except NEO4J_FAILURE as e:
        logger.warning("Neo4j foo failed: %s", e)
        return []

### Read vs Write 시맨틱 (caller 책임)

본 상수는 "어떤 예외를 잡을지" 만 정의한다. **잡은 후 어떻게 처리할지**
는 caller 책임:

- **Read**: ``[]`` / ``{}`` / ``0`` 반환 후 caller 는 degrade (그래프 없이
  검색). `_read_ops.py` / `_search_ops.py` 가 이 패턴.
- **Idempotent write (``IF NOT EXISTS`` / ``MERGE``)**: log + continue 가
  안전. `_ensure_fulltext_index`, `load_nodes_batch` (내부 retry 있음) 이
  이 패턴.
- **비-idempotent write**: 이 상수로 catch 하지 말 것. 데이터 누락이
  조용히 넘어감. Per-item counter + metric + re-raise 패턴 필요.

### Exception 을 쓰지 않는 이유

``Exception`` 은 ``AssertionError`` / ``ZeroDivisionError`` / ``TypeError``
같은 로직 버그까지 삼켜 디버깅 어렵게 만든다. ``Neo4jError`` 명시적 추가가
올바른 범위.
"""

from __future__ import annotations

from neo4j.exceptions import Neo4jError

# Neo4j 호출 fallback — caller 가 read/write 시맨틱 책임.
NEO4J_FAILURE = (
    Neo4jError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
)

# Backward-compat alias — 기존 import `NEO4J_READ_FAILURE` 를 유지해 드리프트
# 를 막는다. 신규 코드는 `NEO4J_FAILURE` 사용.
NEO4J_READ_FAILURE = NEO4J_FAILURE

__all__ = ["NEO4J_FAILURE", "NEO4J_READ_FAILURE"]
