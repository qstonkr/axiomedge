"""Slack notification PII mask — P1-8.

reason 또는 sample text 에 들어갈 수 있는 path/email/IP/token 마스킹 검증.
"""

from __future__ import annotations

from src.notifications.slack import mask_pii


class TestMaskPII:
    def test_user_path(self):
        out = mask_pii("FileNotFound: /Users/alice/secret/key.pem")
        assert "/Users/alice" not in out
        assert "/Users/<USER>" in out

    def test_home_path(self):
        out = mask_pii("permission denied at /home/bob/.config/app")
        assert "/home/bob" not in out
        assert "/home/<USER>" in out

    def test_secrets_path(self):
        out = mask_pii("Cannot read /var/secrets/db_password.txt")
        assert "db_password" not in out
        assert "/var/secrets/<MASKED>" in out

    def test_email(self):
        out = mask_pii("notify alice.smith@example.com about run failure")
        assert "alice.smith@example.com" not in out
        assert "<EMAIL>" in out

    def test_ip_address(self):
        out = mask_pii("connection refused 192.168.1.42:5432")
        assert "192.168.1.42" not in out
        assert "<IP>" in out

    def test_hex_token(self):
        token = "a1b2c3d4e5f6" * 4  # 48자 hex
        out = mask_pii(f"Bearer {token}")
        assert token not in out
        assert "<HEX>" in out

    def test_keeps_normal_text(self):
        # 일반 reason 텍스트는 mask 적용 영역 외 변화 없어야 함
        msg = "embedding dimension mismatch (expected 1024)"
        assert mask_pii(msg) == msg

    def test_empty_string(self):
        assert mask_pii("") == ""

    def test_multiple_patterns_in_one_line(self):
        out = mask_pii("from /Users/x to alice@b.com on 10.0.0.5")
        assert "/Users/<USER>" in out
        assert "<EMAIL>" in out
        assert "<IP>" in out
