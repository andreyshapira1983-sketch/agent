from __future__ import annotations

from pathlib import Path

from core.release_hygiene import build_release_manifest


def test_release_manifest_excludes_local_secrets_and_dev_artifacts(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[remote]\n", encoding="utf-8")
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_text("bin", encoding="utf-8")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "README.md").write_text("cache", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "credentials.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "token.json").write_text("{}", encoding="utf-8")
    (tmp_path / "private.key").write_text("key", encoding="utf-8")

    manifest = build_release_manifest(tmp_path)
    report = manifest.report().to_dict()

    assert "main.py" in {path.relative_to(tmp_path).as_posix() for path in manifest.include_files}
    assert report["ok"] is True
    assert ".env" in report["forbidden_present"]
    assert ".git" in report["forbidden_present"]
    assert ".venv" in report["forbidden_present"]
    assert ".pytest_cache" in report["forbidden_present"]
    assert "config/credentials.json" in report["forbidden_present"]
    assert "config/token.json" in report["forbidden_present"]
    assert "private.key" in report["forbidden_present"]
    assert report["forbidden_included"] == []


def test_release_manifest_flags_forbidden_if_exclusion_is_overridden(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    manifest = build_release_manifest(tmp_path, extra_exclude_files=())
    report = manifest.report().to_dict()

    # The default policy excludes .env, so this remains safe.
    assert report["ok"] is True
    assert report["forbidden_included"] == []


def test_release_hygiene_summary_mentions_forbidden_artifacts(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    summary = build_release_manifest(tmp_path).report().user_summary()

    assert "release hygiene" in summary
    assert "forbidden local artifacts present but excluded" in summary
    assert ".env" in summary
