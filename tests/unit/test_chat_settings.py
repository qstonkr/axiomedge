from src.config import get_settings


def test_chat_settings_defaults(monkeypatch):
    monkeypatch.delenv("CHAT_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("CHAT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("CHAT_AUTO_TITLE_ENABLED", raising=False)
    s = get_settings.__wrapped__()  # bypass lru_cache
    assert s.chat.retention_days == 90
    assert s.chat.auto_title_enabled is True
    assert s.chat.encryption_key == ""  # empty in dev — encryption skipped


def test_chat_settings_env(monkeypatch):
    monkeypatch.setenv("CHAT_ENCRYPTION_KEY", "test-key-32-bytes-aaaaaaaaaaaaaa")
    monkeypatch.setenv("CHAT_RETENTION_DAYS", "30")
    s = get_settings.__wrapped__()
    assert s.chat.retention_days == 30
    assert s.chat.encryption_key == "test-key-32-bytes-aaaaaaaaaaaaaa"
