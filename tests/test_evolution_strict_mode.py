from __future__ import annotations

from src.evolution import safety


def test_validate_candidate_fails_on_warnings_in_strict_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_STRICT_MODE", "1")
    monkeypatch.setattr(safety, "_ROOT", tmp_path)
    monkeypatch.setattr(safety, "_CANDIDATE_DIR", tmp_path / "candidate")
    monkeypatch.setattr(safety, "_MANIFEST", tmp_path / "candidate" / "_manifest.json")

    patch_id = safety.submit_candidate_patch("src/demo.py", "print('x')\n", "test")

    monkeypatch.setattr("src.evolution.sandbox.create_sandbox", lambda root: tmp_path / "sandbox")
    monkeypatch.setattr("src.evolution.sandbox.apply_in_sandbox", lambda sandbox_root, relative_path, content: None)
    monkeypatch.setattr("src.evolution.sandbox.run_pytest_in_sandbox", lambda sandbox_root, timeout=120, test_path="tests/": (True, "=== warning summary ==="))
    monkeypatch.setattr("src.evolution.sandbox.cleanup_sandbox", lambda sandbox_root: None)

    assert safety.validate_candidate_with_tests(patch_id) is False


def test_validate_candidate_passes_without_warnings_in_strict_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_STRICT_MODE", "1")
    monkeypatch.setattr(safety, "_ROOT", tmp_path)
    monkeypatch.setattr(safety, "_CANDIDATE_DIR", tmp_path / "candidate")
    monkeypatch.setattr(safety, "_MANIFEST", tmp_path / "candidate" / "_manifest.json")

    patch_id = safety.submit_candidate_patch("src/demo.py", "print('x')\n", "test")

    monkeypatch.setattr("src.evolution.sandbox.create_sandbox", lambda root: tmp_path / "sandbox")
    monkeypatch.setattr("src.evolution.sandbox.apply_in_sandbox", lambda sandbox_root, relative_path, content: None)
    monkeypatch.setattr("src.evolution.sandbox.run_pytest_in_sandbox", lambda sandbox_root, timeout=120, test_path="tests/": (True, "2 passed"))
    monkeypatch.setattr("src.evolution.sandbox.cleanup_sandbox", lambda sandbox_root: None)

    assert safety.validate_candidate_with_tests(patch_id) is True
