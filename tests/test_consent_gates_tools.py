import importlib


def test_run_powershell_blocked_without_consent(monkeypatch):
    import src.tools  # noqa: F401
    registry = importlib.import_module("src.tools.registry")
    consent = importlib.import_module("src.governance.user_consent")

    monkeypatch.setattr(consent, "is_windows_commands_allowed", lambda: False)

    out = registry.call("run_powershell", script="Get-Location")
    assert "disabled by user consent" in out


def test_fetch_url_blocked_without_consent(monkeypatch):
    tools = importlib.import_module("src.tools.impl.autonomy_tools")
    consent = importlib.import_module("src.governance.user_consent")

    monkeypatch.setattr(consent, "is_internet_allowed", lambda: False)

    out = tools._fetch_url("https://openlibrary.org")
    assert "disabled by user consent" in out
