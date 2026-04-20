"""SecretBox — at-rest encryption abstraction for user-input connector tokens.

축산 정책 (plan):
- 사용자가 UI 로 입력하는 connector token (Confluence PAT, Git auth_token,
  Wiki/Slack/Teams credential 등) 만 SecretBox 로 관리.
- JWT secret / Neo4j password / DB password 같은 인프라 secret 은 env var
  / k8s secret 에 그대로. 프로세스 시작 시 1회 로드 — 멀티테넌트 scope 무관.
- backend default = ``LocalFernetBox`` (cryptography.fernet, on-prem 친화).
  ``VaultBox`` 는 큰 고객사 (FIPS-140, BYOK) 대응 (옵션).

Path namespace 권장: ``org/{org_id}/data-source/{source_id}`` — org-scoped,
immutable. 라우트 핸들러가 OrgContext 에서 ``org.id`` 를 추출해 자동 prefix.

Key 회전 (Fernet): ``SECRET_BOX_KEY`` (current) + ``SECRET_BOX_KEY_PREVIOUS``
(옛 키 fallback decrypt). MultiFernet 가 옛 키로 encrypt 된 토큰을 자동 풀어줌
→ 모든 row re-encrypt 후 PREVIOUS 제거하는 회전 절차.

Vault backend (옵션): ``SECRET_BOX_BACKEND=vault`` 활성화 시 hvac KV v2 사용.
설치: ``uv pip install 'knowledge-local[vault]'`` 또는 ``pip install hvac``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.config import get_settings

if TYPE_CHECKING:
    import hvac  # noqa: F401  # 타입 힌트 전용; 런타임은 lazy import

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
# VaultBox — HashiCorp Vault KV v2 backend (옵션)
# ---------------------------------------------------------------------------


class VaultBox:
    """HashiCorp Vault KV v2 backend — FIPS-140 / HSM / BYOK 요구 고객 대응.

    경로 매핑:
        Protocol path (``org/{org_id}/data-source/{source_id}``)
        → Vault secret path (``{path_prefix}/{path}``)
        → Vault HTTP API (``{addr}/v1/{mount_point}/data/{path_prefix}/{path}``)

    secret payload 는 ``{"value": <plain>}`` 단일 키로 저장 — 호출자는 string
    하나만 다루므로 단순 schema 가 충분.

    인증: 현재는 ``VAULT_TOKEN`` 만 지원. K8s SA / AppRole 은 향후 hvac.auth.*
    helper 추가로 확장 가능 (별도 settings 필드 + factory branch).

    회전: Vault 가 KV v2 의 version 관리를 직접 제공 — application-side rotate
    불필요. ``rotate_token`` 메서드 미구현 (Fernet 전용 회전 패턴).
    """

    def __init__(
        self,
        addr: str,
        token: str,
        mount_point: str = "secret",
        path_prefix: str = "axiomedge",
        namespace: str = "",
    ) -> None:
        if not addr:
            raise SecretBoxError(
                "SECRET_BOX_VAULT_ADDR 가 설정되지 않았습니다 (예: https://vault.example:8200).",
            )
        if not token:
            raise SecretBoxError(
                "SECRET_BOX_VAULT_TOKEN 이 설정되지 않았습니다 — "
                "Vault token 또는 K8s SA bound token 을 env 에 주입하세요.",
            )
        try:
            import hvac  # noqa: PLC0415
            from hvac.exceptions import (  # noqa: PLC0415
                Forbidden,
                InvalidPath,
            )
        except ImportError as e:
            # hvac 미설치 또는 호환되지 않는 버전 (예외 클래스가 이동/제거된
            # 경우). silent fallback (Exception 매칭) 보다 명시적 실패가 안전 —
            # get/delete 가 모든 예외를 None 으로 흡수하면 connection 에러도
            # "secret 없음" 으로 오해되어 라우트 fallback 이 잘못 동작.
            raise SecretBoxError(
                "hvac 라이브러리가 설치되지 않았거나 호환되지 않는 버전입니다. "
                "`uv pip install 'knowledge-local[vault]'` 또는 `pip install hvac>=2.0`.",
            ) from e

        # 캐싱 — get/delete 가 isinstance 로 분기 (문자열 ``type(e).__name__``
        # 비교 X). 위 from-import 가 보장하므로 fallback 분기 불필요.
        self._exc_invalid_path: type[Exception] = InvalidPath
        self._exc_forbidden: type[Exception] = Forbidden

        self._mount_point = mount_point.strip("/")
        self._path_prefix = path_prefix.strip("/")
        kwargs: dict[str, Any] = {"url": addr, "token": token}
        if namespace:
            kwargs["namespace"] = namespace
        self._client = hvac.Client(**kwargs)
        # 시작 시 인증 검증 — fail-closed.
        try:
            if not self._client.is_authenticated():
                raise SecretBoxError("Vault 인증 실패 — token 만료/권한 부족 가능성.")
        except SecretBoxError:
            raise
        except Exception as e:  # noqa: BLE001 — hvac 가 다양한 connection/auth 예외 raise (requests.ConnectionError, urllib3.exceptions.*, hvac.exceptions.*)
            raise SecretBoxError(f"Vault 연결 실패: {e}") from e

    def _full_path(self, path: str) -> str:
        path = path.strip("/")
        if self._path_prefix:
            return f"{self._path_prefix}/{path}"
        return path

    async def put(self, path: str, value: str) -> None:
        """KV v2 create_or_update — 같은 path 면 새 version 생성 (Vault 가 history 보관)."""
        try:
            self._client.secrets.kv.v2.create_or_update_secret(
                mount_point=self._mount_point,
                path=self._full_path(path),
                secret={"value": value},
            )
        except Exception as e:  # noqa: BLE001 — hvac 다양한 예외 통합 (Forbidden/InvalidPath/Connection)
            logger.exception("VaultBox.put 실패: path=%s", path)
            raise SecretBoxError(f"Vault put 실패: {e}") from e

    async def get(self, path: str) -> str | None:
        """KV v2 read_secret_version — 미존재 시 None (404 → InvalidPath 흡수)."""
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                mount_point=self._mount_point,
                path=self._full_path(path),
                raise_on_deleted_version=False,
            )
        except (self._exc_invalid_path, self._exc_forbidden):
            # 미존재 / 권한 거부 — 라우트가 fallback 처리. SecretBox 입장에서는 None.
            return None
        except Exception as e:  # noqa: BLE001 — hvac 의 Connection/Server 예외 통합
            logger.exception("VaultBox.get 실패: path=%s", path)
            raise SecretBoxError(f"Vault get 실패: {e}") from e

        try:
            return resp["data"]["data"]["value"]
        except (KeyError, TypeError):
            return None

    async def delete(self, path: str) -> None:
        """KV v2 delete_metadata_and_all_versions — 모든 version + metadata 영구 삭제."""
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                mount_point=self._mount_point,
                path=self._full_path(path),
            )
        except self._exc_invalid_path:
            # 미존재는 idempotent — 조용히 통과.
            return
        except Exception as e:  # noqa: BLE001 — hvac 의 Connection/Forbidden 예외 통합
            logger.exception("VaultBox.delete 실패: path=%s", path)
            raise SecretBoxError(f"Vault delete 실패: {e}") from e


# ---------------------------------------------------------------------------
# Factory + module cache
# ---------------------------------------------------------------------------

_box: SecretBox | None = None


def get_secret_box() -> SecretBox:
    """Settings 기반 backend 선택 + module-level 캐시.

    backend=fernet (default): LocalFernetBox 인스턴스. KEY 미설정 시 startup
    실패 — fail-closed (running with no encryption 보다는 명시적 실패가 안전).

    backend=vault (옵션): VaultBox — hvac 미설치 또는 ADDR/TOKEN 미설정 시 raise.
    """
    global _box
    if _box is not None:
        return _box

    s = get_settings().secret_box
    if s.backend == "fernet":
        _box = LocalFernetBox(s.key, s.key_previous)
    elif s.backend == "vault":
        _box = VaultBox(
            addr=s.vault_addr,
            token=s.vault_token,
            mount_point=s.vault_mount_point,
            path_prefix=s.vault_path_prefix,
            namespace=s.vault_namespace,
        )
    else:
        raise SecretBoxError(f"Unknown SECRET_BOX_BACKEND: {s.backend!r}")
    return _box


def reset_secret_box() -> None:
    """테스트용 — module cache clear (key 변경 후 재초기화)."""
    global _box
    _box = None
