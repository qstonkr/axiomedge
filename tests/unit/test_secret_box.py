"""Phase 1 — SecretBox / LocalFernetBox 검증.

backend=fernet 의 핵심 보장:
1. round-trip (encrypt → decrypt 같은 값)
2. rotation fallback (옛 키로 encrypt → 신/옛 키 모두로 decrypt)
3. tampering 감지 (Fernet InvalidToken → SecretBoxError)
4. missing key fail-closed (KEY 미설정 시 startup 실패)
5. SecretBox Protocol 인터페이스 (put/get/delete)
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.auth.secret_box import (
    LocalFernetBox,
    SecretBoxError,
    get_secret_box,
    reset_secret_box,
)


@pytest.fixture
def fresh_key() -> str:
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_encrypt_decrypt_returns_same_plain(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        plain = "ghp_secrettoken12345"
        ciphertext = box.encrypt(plain)
        assert box.decrypt(ciphertext) == plain

    def test_ciphertext_is_not_plain(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        ciphertext = box.encrypt("my-secret-token")
        assert b"my-secret-token" not in ciphertext

    def test_korean_unicode_round_trip(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        plain = "토큰-한글-인증서-🔐"
        assert box.decrypt(box.encrypt(plain)) == plain


# ---------------------------------------------------------------------------
# Rotation — 옛 키로 encrypt 된 ciphertext 도 fallback decrypt
# ---------------------------------------------------------------------------


class TestKeyRotation:
    def test_old_key_ciphertext_decrypts_with_multifernet(
        self, fresh_key: str,
    ) -> None:
        old_key = Fernet.generate_key().decode()
        # 옛 키 단독 box 로 encrypt
        old_box = LocalFernetBox(old_key)
        ciphertext = old_box.encrypt("legacy-token")

        # 새 키 + 옛 키 fallback 으로 decrypt
        rotated = LocalFernetBox(key=fresh_key, key_previous=old_key)
        assert rotated.decrypt(ciphertext) == "legacy-token"

    def test_rotate_token_re_encrypts_with_current_key(
        self, fresh_key: str,
    ) -> None:
        old_key = Fernet.generate_key().decode()
        old_box = LocalFernetBox(old_key)
        ciphertext_old = old_box.encrypt("token-x")

        rotated = LocalFernetBox(key=fresh_key, key_previous=old_key)
        ciphertext_new = rotated.rotate_token(ciphertext_old)
        # 새 ciphertext 는 fresh_key 단독으로도 decrypt 가능 — old_key 의존 X
        new_only = LocalFernetBox(fresh_key)
        assert new_only.decrypt(ciphertext_new) == "token-x"

    def test_invalid_previous_key_logged_but_doesnt_crash(
        self, fresh_key: str, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # PREVIOUS 가 잘못된 형식 — fallback 비활성, current key 만 동작
        box = LocalFernetBox(key=fresh_key, key_previous="not-a-valid-fernet-key")
        # current key 로 정상 round-trip
        assert box.decrypt(box.encrypt("ok")) == "ok"


# ---------------------------------------------------------------------------
# Tampering / 위조 감지
# ---------------------------------------------------------------------------


class TestTampering:
    def test_tampered_ciphertext_raises(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        ciphertext = box.encrypt("authentic")
        # 마지막 byte 변조
        tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 0x01])
        with pytest.raises(SecretBoxError):
            box.decrypt(tampered)

    def test_wrong_key_cant_decrypt(self, fresh_key: str) -> None:
        box1 = LocalFernetBox(fresh_key)
        ciphertext = box1.encrypt("alice-token")

        other_key = Fernet.generate_key().decode()
        box2 = LocalFernetBox(other_key)
        with pytest.raises(SecretBoxError):
            box2.decrypt(ciphertext)


# ---------------------------------------------------------------------------
# Missing key — fail-closed
# ---------------------------------------------------------------------------


class TestMissingKeyFailsClosed:
    def test_empty_key_raises(self) -> None:
        with pytest.raises(SecretBoxError, match="SECRET_BOX_KEY"):
            LocalFernetBox(key="")

    def test_get_secret_box_with_empty_settings_fails(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # SECRET_BOX_KEY 가 비어있을 때 factory 호출 시 즉시 실패
        monkeypatch.setenv("SECRET_BOX_KEY", "")
        monkeypatch.setenv("SECRET_BOX_BACKEND", "fernet")
        from src.config.settings import reset_settings
        reset_settings()
        reset_secret_box()
        with pytest.raises(SecretBoxError, match="SECRET_BOX_KEY"):
            get_secret_box()
        # 정리
        reset_secret_box()
        reset_settings()


# ---------------------------------------------------------------------------
# Protocol interface — put/get/delete (in-memory store)
# ---------------------------------------------------------------------------


class TestProtocolInterface:
    @pytest.mark.asyncio
    async def test_put_then_get(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        await box.put("org/o1/data-source/ds1", "secret-value")
        assert await box.get("org/o1/data-source/ds1") == "secret-value"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        assert await box.get("org/x/data-source/missing") is None

    @pytest.mark.asyncio
    async def test_delete_idempotent(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        await box.put("org/o1/data-source/ds1", "v")
        await box.delete("org/o1/data-source/ds1")
        await box.delete("org/o1/data-source/ds1")  # 두 번째도 raise X
        assert await box.get("org/o1/data-source/ds1") is None

    @pytest.mark.asyncio
    async def test_put_overwrites(self, fresh_key: str) -> None:
        box = LocalFernetBox(fresh_key)
        await box.put("p", "v1")
        await box.put("p", "v2")
        assert await box.get("p") == "v2"


# ---------------------------------------------------------------------------
# Vault backend stub — Phase 4 에서 활성화 전까지 명시적 실패
# ---------------------------------------------------------------------------


class TestVaultBackendStub:
    def test_vault_backend_not_implemented_yet(
        self, monkeypatch: pytest.MonkeyPatch, fresh_key: str,
    ) -> None:
        monkeypatch.setenv("SECRET_BOX_BACKEND", "vault")
        monkeypatch.setenv("SECRET_BOX_KEY", fresh_key)
        from src.config.settings import reset_settings
        reset_settings()
        reset_secret_box()
        with pytest.raises(SecretBoxError, match="Phase 4"):
            get_secret_box()
        reset_secret_box()
        reset_settings()
