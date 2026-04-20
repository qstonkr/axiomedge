"""SecretBox — at-rest encryption abstraction for user-input connector tokens.

축산 정책 (plan):
- 사용자가 UI 로 입력하는 connector token (Confluence PAT, Git auth_token,
  Wiki/Slack/Teams credential 등) 만 SecretBox 로 관리.
- JWT secret / Neo4j password / DB password 같은 인프라 secret 은 env var
  / k8s secret 에 그대로. 프로세스 시작 시 1회 로드 — 멀티테넌트 scope 무관.
- backend default = ``LocalFernetBox`` (cryptography.fernet, on-prem 친화).
  ``VaultBox`` 는 큰 고객사 (FIPS-140, BYOK) 대응으로 후속 (Phase 4).

Path namespace 권장: ``org/{org_id}/data-source/{source_id}`` — org-scoped,
immutable. 라우트 핸들러가 OrgContext 에서 ``org.id`` 를 추출해 자동 prefix.

Key 회전: ``SECRET_BOX_KEY`` (current) + ``SECRET_BOX_KEY_PREVIOUS`` (옛 키
fallback decrypt). MultiFernet 가 옛 키로 encrypt 된 토큰을 자동 풀어줌
→ 모든 row re-encrypt 후 PREVIOUS 제거하는 회전 절차.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.config import get_settings

logger = logging.getLogger(__name__)


class SecretBoxError(RuntimeError):
    """SecretBox 작업 실패. detail 은 logger 에만, 호출자에게는 안전 메시지."""


@runtime_checkable
class SecretBox(Protocol):
    """Secret 의 at-rest 저장 backend 추상화.

    구현은 ``LocalFernetBox`` (default) 또는 ``VaultBox`` (옵션). path 는
    ``org/{org_id}/data-source/{source_id}`` 같은 org-scoped immutable
    string — backend 별 path mapping 은 구현체가 처리.
    """

    async def put(self, path: str, value: str) -> None:
        """Encrypt + store. 같은 path 가 이미 있으면 덮어씀 (회전 시점 별 row 1개)."""
        ...

    async def get(self, path: str) -> str | None:
        """Decrypt + return plain. 미존재 시 None (라우트가 fallback / 404 매핑)."""
        ...

    async def delete(self, path: str) -> None:
        """Path 삭제. 미존재 시도 idempotent."""
        ...


class LocalFernetBox:
    """In-process Fernet — DB 컬럼에 ciphertext 직접 저장.

    동작 모델:
    1. ``put(path, value)`` → ``ciphertext = MultiFernet.encrypt(value)`` →
       caller 에게 ciphertext 반환 대신 별도 store API 가 ciphertext 를 보관.
       → 본 클래스는 **encrypt/decrypt 만** 담당. 실제 path→ciphertext 매핑
       은 호출자 (data_sources 라우트) 가 DB column 에 저장.

    이 분리 덕에 같은 클래스가 어떤 storage 위에서도 작동 — DB BYTEA, S3,
    또는 in-memory dict 모두 OK.

    그러나 SecretBox Protocol 시그니처가 path → value 매핑을 요구하므로
    in-memory dict 를 기본 store 로 두고, 프로덕션 wrapper 가 DB 에 위임.
    Phase 2 에서 data_sources route 가 DB-backed wrapper 를 만들어 사용.
    """

    def __init__(self, key: str, key_previous: str = "") -> None:
        if not key:
            raise SecretBoxError(
                "SECRET_BOX_KEY 가 설정되지 않았습니다. "
                "`python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'` 로 생성 후 env 에 설정하세요.",
            )
        keys: list[Fernet] = [Fernet(key.encode())]
        if key_previous:
            try:
                keys.append(Fernet(key_previous.encode()))
            except (ValueError, TypeError) as e:
                logger.warning(
                    "SECRET_BOX_KEY_PREVIOUS 파싱 실패 (회전 fallback 비활성): %s", e,
                )
        self._fernet = MultiFernet(keys)
        # in-memory store — Phase 2 의 DB-backed wrapper 가 이 클래스 대신 사용.
        # 단위 테스트에서 round-trip 검증용으로만 의미 있음.
        self._store: dict[str, bytes] = {}

    def encrypt(self, value: str) -> bytes:
        """Plain → ciphertext bytes. Caller (DB store) 가 보관."""
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        """Ciphertext → plain. 옛 키 (PREVIOUS) 로 encrypt 된 것도 자동 처리."""
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as e:
            raise SecretBoxError("ciphertext 위조 또는 키 불일치") from e

    def rotate_token(self, ciphertext: bytes) -> bytes:
        """옛 키로 encrypt 된 token 을 현재 키로 re-encrypt. 회전 절차에서 사용."""
        return self._fernet.rotate(ciphertext)

    # --- SecretBox Protocol (in-memory store, dev/test 전용) ---

    async def put(self, path: str, value: str) -> None:
        self._store[path] = self.encrypt(value)

    async def get(self, path: str) -> str | None:
        ciphertext = self._store.get(path)
        if ciphertext is None:
            return None
        return self.decrypt(ciphertext)

    async def delete(self, path: str) -> None:
        self._store.pop(path, None)


# ---------------------------------------------------------------------------
# Factory + module cache
# ---------------------------------------------------------------------------

_box: SecretBox | None = None


def get_secret_box() -> SecretBox:
    """Settings 기반 backend 선택 + module-level 캐시.

    backend=fernet (default): LocalFernetBox 인스턴스. KEY 미설정 시 startup
    실패 — fail-closed (running with no encryption 보다는 명시적 실패가 안전).

    backend=vault: Phase 4 에서 VaultBox 추가 시 활성화.
    """
    global _box
    if _box is not None:
        return _box

    s = get_settings().secret_box
    if s.backend == "fernet":
        _box = LocalFernetBox(s.key, s.key_previous)
    elif s.backend == "vault":
        # Phase 4 — hvac client 설치 후 활성화.
        raise SecretBoxError(
            "VaultBox 는 Phase 4 에서 추가 예정. SECRET_BOX_BACKEND=fernet 사용.",
        )
    else:
        raise SecretBoxError(f"Unknown SECRET_BOX_BACKEND: {s.backend!r}")
    return _box


def reset_secret_box() -> None:
    """테스트용 — module cache clear (key 변경 후 재초기화)."""
    global _box
    _box = None
