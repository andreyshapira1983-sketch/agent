"""
Evolution safety: self-modification only via sandbox validation.

Правило: применять к основной кодовой базе можно только артефакты из песочницы
с меткой «прошло тесты» (validated). Если не помечено — accept_patch_to_stable
возвращает ошибку. Никакой прямой записи в стабильный код без validate.

Architecture rule: auto_patch must not modify running agent directly.
Flow: submit_candidate_patch → validate_candidate_with_tests (в sandbox) →
accept_patch_to_stable. Все проверки патча выполняются в копии проекта (sandbox).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

_ROOT = Path(__file__).resolve().parent.parent.parent
_CANDIDATE_DIR = _ROOT / "config" / "candidate_patches"
_MANIFEST = _CANDIDATE_DIR / "_manifest.json"


def _strict_mode_enabled() -> bool:
    import os
    return (os.environ.get("AGENT_STRICT_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")


def _has_pytest_warnings(output: str) -> bool:
    low = (output or "").lower()
    return "warning summary" in low or " warnings " in low or "warning:" in low


def _normalize_relative_path(path: str) -> str:
    """Путь относительно корня проекта, с forward slashes."""
    p = (_ROOT / path.lstrip("/")).resolve()
    if not str(p).startswith(str(_ROOT)):
        raise ValueError(f"Path outside project: {path}")
    return p.relative_to(_ROOT).as_posix()


def _ensure_candidate_dir() -> Path:
    _CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    return _CANDIDATE_DIR


def _load_manifest() -> list[dict]:
    if not _MANIFEST.exists():
        return []
    try:
        return cast(list[dict], json.loads(_MANIFEST.read_text(encoding="utf-8")))
    except Exception:
        return []


def _save_manifest(entries: list[dict]) -> None:
    _ensure_candidate_dir()
    _MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def submit_candidate_patch(target_path: str, content: str, reason: str = "") -> str:
    """
    Save a patch to candidate_patches for sandbox validation. Does not touch live code.
    Returns patch_id. Next: run validate_candidate_with_tests(patch_id), then accept_patch_to_stable(patch_id).
    """
    try:
        from src.governance.patch_guard import can_patch
        allowed, msg = can_patch(target_path)
        if not allowed:
            return f"Error: {msg}"
    except Exception:
        pass
    _ensure_candidate_dir()
    safe_name = target_path.replace("/", "_").replace("\\", "_")[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    patch_id = f"{ts}_{safe_name}"
    patch_file = _CANDIDATE_DIR / f"{patch_id}.patch"
    meta = {
        "patch_id": patch_id,
        "target_path": target_path,
        "reason": reason[:200],
        "validated": False,
        "created_at": ts,
    }
    patch_file.write_text(content, encoding="utf-8")
    manifest = _load_manifest()
    manifest.append(meta)
    _save_manifest(manifest)
    return patch_id


def validate_candidate_with_tests(patch_id: str, test_cmd: list[str] | None = None) -> bool:
    """
    Валидация патча в sandbox: копия проекта → применить патч → pytest.
    Если тесты проходят, патч помечается validated. В основной проект не пишем.
    """
    manifest = _load_manifest()
    entry = next((m for m in manifest if m.get("patch_id") == patch_id), None)
    if not entry:
        return False
    patch_file = _CANDIDATE_DIR / f"{patch_id}.patch"
    if not patch_file.exists():
        return False
    target_path = entry.get("target_path") or ""
    content = patch_file.read_text(encoding="utf-8")
    try:
        rel_path = _normalize_relative_path(target_path)
    except ValueError:
        return False
    from src.evolution.sandbox import create_sandbox, apply_in_sandbox, run_pytest_in_sandbox, cleanup_sandbox
    sandbox_path = create_sandbox(_ROOT)
    try:
        apply_in_sandbox(sandbox_path, rel_path, content)
        test_path = "tests/"
        if test_cmd and len(test_cmd) > 1:
            # test_cmd[1:] может быть путём к тестам — не используем для совместимости
            pass
        ok, out = run_pytest_in_sandbox(sandbox_path, timeout=120, test_path=test_path)
        if not ok:
            return False
        if _strict_mode_enabled() and _has_pytest_warnings(out):
            return False
        for m in manifest:
            if m.get("patch_id") == patch_id:
                m["validated"] = True
                break
        _save_manifest(manifest)
        return True
    finally:
        cleanup_sandbox(sandbox_path)


def accept_patch_to_stable(patch_id: str) -> str:
    """
    Применить валидированный патч к живому проекту. Вызывать только после validate.
    Опционально: при EVOLUTION_ACCEPT_SANDBOX=1 перед записью ещё раз проверка в sandbox.
    """
    import os
    manifest = _load_manifest()
    entry = next((m for m in manifest if m.get("patch_id") == patch_id), None)
    if not entry:
        return f"Patch {patch_id} not found."
    if not entry.get("validated"):
        return "Patch must be validated (run tests in sandbox) before accept."
    # Архитектурный чек: запрещённые пути (config, bootstrap, governance, hitl)
    try:
        from src.governance.policy_engine import PolicyEngine
        engine = PolicyEngine()
        if not engine.is_path_allowed(entry.get("target_path") or ""):
            return f"Path is protected (architecture): {entry.get('target_path')}. Cannot accept patch to this path."
    except Exception:
        pass
    try:
        from src.governance.task_guard import can_accept_evolution_patch, record_evolution_accept
        allowed, msg = can_accept_evolution_patch()
        if not allowed:
            return f"Error: {msg}"
    except Exception:
        pass
    try:
        from src.governance.evolution_lock import acquire, release, get_holder
        from src.state.agent_state import get_state
        agent_id = (get_state() or {}).get("agent_id", "root")
        if not acquire(agent_id):
            holder = get_holder() or "unknown"
            return f"Error: another agent is applying a patch (holder: {holder}). Wait and try again (coordination)."
    except Exception:
        pass
    try:
        patch_file = _CANDIDATE_DIR / f"{patch_id}.patch"
        if not patch_file.exists():
            return f"Patch file missing: {patch_id}.patch"
        content = patch_file.read_text(encoding="utf-8")
        target_path = entry["target_path"].lstrip("/")
        try:
            rel_path = _normalize_relative_path(entry["target_path"])
        except ValueError:
            return f"Invalid target path: {entry['target_path']}"
        if os.environ.get("EVOLUTION_ACCEPT_SANDBOX", "0") == "1":
            from src.evolution.sandbox import run_in_sandbox, cleanup_sandbox
            ok, msg, sandbox_path = run_in_sandbox(_ROOT, rel_path, content, timeout=120)
            if not ok:
                return f"Accept aborted: sandbox check failed.\n{msg}"
            if sandbox_path:
                cleanup_sandbox(sandbox_path)
        target = _ROOT / target_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"Failed to write {target}: {e}"
        try:
            from src.governance.patch_guard import record_patch
            record_patch(target_path)
        except Exception:
            pass
        try:
            from src.governance.task_guard import record_evolution_accept
            record_evolution_accept()
        except Exception:
            pass
        try:
            from src.monitoring.metrics import metrics
            metrics.record_patch_accepted()
        except Exception:
            pass
        # Remove from manifest so it is not applied again
        manifest = [m for m in _load_manifest() if m.get("patch_id") != patch_id]
        _save_manifest(manifest)
        try:
            patch_file.unlink()
        except OSError as e:
            logging.getLogger(__name__).debug("Could not unlink patch file %s: %s", patch_id, e)
        return f"Patch {patch_id} applied to {entry['target_path']}."
    finally:
        try:
            from src.governance.evolution_lock import release
            release()
        except Exception:
            pass


def get_validated_patch_ids() -> list[str]:
    """Список patch_id, которые уже прошли validate (можно применять accept)."""
    manifest = _load_manifest()
    return [m["patch_id"] for m in manifest if m.get("validated")]


def apply_all_validated(max_n: int = 20) -> list[dict]:
    """
    Применить все проверенные (validated) патчи из песочницы. Возвращает список
    {patch_id, target_path, status: "ok" | "error", message}.
    Для команды /apply_validated в Telegram — пользователю не нужно программировать.
    """
    ids = get_validated_patch_ids()[:max_n]
    results: list[dict] = []
    for patch_id in ids:
        entry = next((m for m in _load_manifest() if m.get("patch_id") == patch_id), None)
        target = (entry or {}).get("target_path", patch_id)
        msg = accept_patch_to_stable(patch_id)
        if "applied to" in msg:
            results.append({"patch_id": patch_id, "target_path": target, "status": "ok", "message": msg})
        else:
            results.append({"patch_id": patch_id, "target_path": target, "status": "error", "message": msg})
    return results


def forbid_direct_apply() -> None:
    """
    Call this from any path that would apply a patch directly to running agent code.
    Raises to enforce: patch must go through candidate → validate → accept.
    """
    raise RuntimeError(
        "Patch must not be applied to running agent directly. "
        "Use evolution.safety: submit_candidate_patch → validate_candidate_with_tests → accept_patch_to_stable."
    )
