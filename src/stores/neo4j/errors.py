"""Shared exception tuple for Neo4j call sites.

### 배경

과거 Neo4j 호출을 감싸는 22곳이 아래 tuple 을 복사-붙여넣기로 사용했다:

    except (RuntimeError, OSError, ValueError, TypeError, KeyError, AttributeError)

이 tuple 은 **``neo4j.exceptions`` 계열 전체를 놓쳤다** — ``Neo4jError``
(서버 측 쿼리 에러) 와 ``DriverError`` (클라이언트 측 연결/세션 에러) 둘 다.
2026-04-23 ``search_section_titles`` scope 버그가 이 갭에 의해 답변 실패까지
번진 사례.

### ``neo4j.exceptions`` 계층 (2026-04-24 검증)

    GqlError (root)
      ├── Neo4jError       — 서버 측 응답 에러
      │   ├── ClientError
      │   │   ├── CypherSyntaxError
      │   │   └── ConstraintError
      │   ├── DatabaseError
      │   └── TransientError  (leader election 등 재시도 가능)
      └── DriverError      — 클라이언트 측 전송/세션 에러
          ├── ServiceUnavailable   ← bolt drop, pod restart, failover
          └── SessionExpired       ← pool 만료

``Neo4jError`` 만 잡으면 **가장 흔한** 연결 장애 (``ServiceUnavailable``)
가 빠진다. 본 상수는 두 계층 모두 포함한다.

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
  검색). ``_read_ops.py`` / ``_search_ops.py`` 가 이 패턴.
- **Idempotent write (``IF NOT EXISTS`` / ``MERGE``)**: log + continue 가
  안전. ``_ensure_fulltext_index``, ``load_nodes_batch`` (내부 retry 있음)
  이 이 패턴.
- **비-idempotent write**: 이 상수로 catch 하지 말 것. 데이터 누락이
  조용히 넘어감. Per-item counter + metric + re-raise 패턴 필요.

### Exception 을 쓰지 않는 이유

``Exception`` 은 ``AssertionError`` / ``ZeroDivisionError`` / ``TypeError``
같은 로직 버그까지 삼켜 디버깅 어렵게 만든다. ``Neo4jError`` +
``DriverError`` 명시적 추가가 올바른 범위.
"""

from __future__ import annotations

from neo4j.exceptions import DriverError, Neo4jError

# Neo4j 호출 fallback — caller 가 read/write 시맨틱 책임.
# Neo4jError: 서버 측 에러 (CypherSyntaxError, ClientError, TransientError 등).
# DriverError: 클라이언트 측 에러 (ServiceUnavailable, SessionExpired 등).
NEO4J_FAILURE = (
    Neo4jError,
    DriverError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
)

# Backward-compat alias. 초기 도입 시 ``NEO4J_READ_FAILURE`` 로 명명했으나 write
# path 에서도 쓰여 혼동 소지 → ``NEO4J_FAILURE`` 로 rename. 외부 소비자 안전망
# 으로 alias 유지.
NEO4J_READ_FAILURE = NEO4J_FAILURE

__all__ = ["NEO4J_FAILURE", "NEO4J_READ_FAILURE"]
