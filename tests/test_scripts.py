"""Tests for the operator scripts/ — they were at 0% coverage.

These are deterministic, no-LLM, no-network helper CLIs. We pin:
  - generate_sbom.py: write + --check (in sync / out of sync / missing),
  - audit_release.py: a smoke run on the real repo (read-only, deterministic).
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path


# ============================================================
# scripts/generate_sbom.py
# ============================================================

import scripts.generate_sbom as gs


_LOCK = (
    "# locked deps\n"
    "certifi==2024.2.2 \\\n"
    "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    "idna==3.6 \\\n"
    "    --hash=sha256:1111111111111111111111111111111111111111111111111111111111111111\n"
)


class TestGenerateSbom:
    def _setup(self, tmp_path: Path, monkeypatch) -> Path:
        (tmp_path / "requirements.lock").write_text(_LOCK, encoding="utf-8")
        monkeypatch.setattr(gs, "ROOT", tmp_path)
        return tmp_path

    def test_write_then_check_in_sync(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        assert gs.main([]) == 0
        assert (tmp_path / "sbom.cdx.json").exists()
        capsys.readouterr()
        # A check immediately after a write must report in-sync.
        assert gs.main(["--check"]) == 0
        assert "in sync" in capsys.readouterr().out

    def test_check_out_of_sync(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        assert gs.main([]) == 0
        (tmp_path / "sbom.cdx.json").write_text("{}", encoding="utf-8")
        assert gs.main(["--check"]) == 1
        assert "out of sync" in capsys.readouterr().err

    def test_check_missing_output(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        # No prior write → output missing.
        assert gs.main(["--check"]) == 1
        assert "missing" in capsys.readouterr().err

    def test_written_sbom_is_valid_cyclonedx(self, tmp_path: Path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        gs.main([])
        capsys.readouterr()
        payload = json.loads((tmp_path / "sbom.cdx.json").read_text(encoding="utf-8"))
        assert payload["bomFormat"] == "CycloneDX"
        names = {c["name"] for c in payload["components"]}
        assert {"certifi", "idna"} <= names


# ============================================================
# scripts/audit_release.py — smoke run on the real repo (read-only)
# ============================================================

class TestAuditRelease:
    def test_main_runs_and_returns_pass_or_fail(self, capsys):
        import scripts.audit_release as ar
        importlib.reload(ar)
        rc = ar.main()
        assert rc in (0, 1)
        payload = json.loads(capsys.readouterr().out)
        assert "release_hygiene" in payload
        assert "supply_chain" in payload
