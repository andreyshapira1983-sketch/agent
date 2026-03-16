from src.governance import user_consent


def test_user_consent_persist_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(user_consent, "_STORAGE", tmp_path / "user_consent.json")
    monkeypatch.setattr(user_consent, "_state", None)

    assert user_consent.is_internet_allowed() is False
    assert user_consent.is_windows_commands_allowed() is False

    user_consent.set_internet_allowed(True)
    user_consent.set_windows_commands_allowed(True)

    monkeypatch.setattr(user_consent, "_state", None)

    assert user_consent.is_internet_allowed() is True
    assert user_consent.is_windows_commands_allowed() is True


def test_user_consent_status_text(tmp_path, monkeypatch):
    monkeypatch.setattr(user_consent, "_STORAGE", tmp_path / "user_consent.json")
    monkeypatch.setattr(user_consent, "_state", None)

    text = user_consent.get_consent_status_text()
    assert "интернет" in text.lower()
    assert "windows" in text.lower()
