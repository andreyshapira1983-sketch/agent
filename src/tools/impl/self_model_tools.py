"""
Tools for Self-Model: analyze_self_model, update_self_model, generate_module_skeleton.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.tools.base import tool_schema
from src.tools.registry import register


logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _config_dir() -> Path:
    return _project_root() / "config"


def _templates_dir() -> Path:
    return _project_root() / "templates"


def _layout_validation_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "symbol_check_mode": "strict",
        "symbol_check_whitelist": [],
        "summary_path": "test-results/layout_validation_summary.json",
        "summary_top_n": 20,
    }
    path = _config_dir() / "layout_validation.json"
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return defaults

    if not isinstance(data, dict):
        return defaults

    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    active = (os.environ.get("AGENT_VALIDATION_PROFILE") or data.get("active_profile") or "").strip()
    if active and active in profiles and isinstance(profiles.get(active), dict):
        profile_data = profiles.get(active) or {}
        merged = dict(data)
        merged.update(profile_data)
        data = merged

    mode = str(data.get("symbol_check_mode", defaults["symbol_check_mode"]))
    mode = mode.strip().lower()
    if mode in {"strict", "relaxed", "off"}:
        defaults["symbol_check_mode"] = mode

    whitelist = data.get("symbol_check_whitelist", defaults["symbol_check_whitelist"])
    if isinstance(whitelist, list):
        defaults["symbol_check_whitelist"] = [str(x).strip() for x in whitelist if str(x).strip()]
    elif isinstance(whitelist, str):
        defaults["symbol_check_whitelist"] = [x.strip() for x in whitelist.split(",") if x.strip()]

    summary_path = data.get("summary_path", defaults["summary_path"])
    if isinstance(summary_path, str) and summary_path.strip():
        defaults["summary_path"] = summary_path.strip().replace("\\", "/")

    top_n = data.get("summary_top_n", defaults["summary_top_n"])
    try:
        defaults["summary_top_n"] = max(1, int(top_n))
    except (TypeError, ValueError):
        pass

    return defaults


def _snake_to_camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def _trace(lines: list[str], level: str, message: str) -> None:
    lvl = (level or "info").lower()
    if lvl == "error":
        logger.error(message)
        tag = "ERROR"
    elif lvl == "warning":
        logger.warning(message)
        tag = "WARN"
    else:
        logger.info(message)
        tag = "OK"
    lines.append(f"[{tag}] {message}")


def _candidate_main_names(module_name: str) -> list[str]:
    candidates = [module_name]
    camel = _snake_to_camel(module_name)
    if camel and camel not in candidates:
        candidates.append(camel)
    title = module_name.capitalize()
    if title and title not in candidates:
        candidates.append(title)
    return candidates


def _collect_python_files(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(
        p for p in base.rglob("*.py")
        if p.is_file() and p.name != "__init__.py"
    )


def _collect_top_level_test_names(test_files: list[Path]) -> dict[str, list[str]]:
    test_name_to_files: dict[str, list[str]] = {}
    for test_file in test_files:
        try:
            tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                test_name_to_files.setdefault(node.name, []).append(test_file.as_posix())
    return test_name_to_files


def _check_main_symbols(file_path: Path, expected_symbols: list[str] | None = None) -> dict[str, Any]:
    module_name = file_path.stem
    candidates = expected_symbols or _candidate_main_names(module_name)
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, UnicodeDecodeError, OSError, ValueError) as e:
        return {
            "ok": False,
            "error": f"Cannot parse {file_path.as_posix()}: {e}",
            "candidates": candidates,
            "present": [],
        }

    present: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            present.add(node.name)
    matched = sorted(name for name in candidates if name in present)
    return {
        "ok": bool(matched),
        "candidates": candidates,
        "present": sorted(present),
        "matched": matched,
    }


def _is_whitelisted(rel_path: str, whitelist: list[str]) -> bool:
    if not whitelist:
        return False
    normalized = rel_path.replace("\\", "/").lstrip("/")
    src_rel = normalized[4:] if normalized.startswith("src/") else normalized
    for raw in whitelist:
        pattern = (raw or "").strip().replace("\\", "/")
        if not pattern:
            continue
        if pattern.endswith("/"):
            if normalized.startswith(pattern) or src_rel.startswith(pattern.rstrip("/") + "/"):
                return True
            continue
        if pattern in (normalized, src_rel):
            return True
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(src_rel, pattern):
            return True
    return False


def _severity(item_type: str) -> int:
    mapping = {
        "symbol_parse_error": 95,
        "duplicate_test_name": 90,
        "duplicate_src_filename": 85,
        "duplicate_test_filename": 80,
        "missing_test_file": 70,
        "missing_test_dir": 60,
        "symbol_mismatch": 50,
    }
    return mapping.get(item_type, 40)


def _write_top_issues_summary(
    root: Path,
    summary_path: str,
    counts: dict[str, int],
    symbol_issues: list[dict[str, Any]],
    duplicate_src_files: dict[str, list[str]],
    duplicate_test_files: dict[str, list[str]],
    duplicate_test_names: dict[str, list[str]],
    nesting_issues: list[dict[str, Any]],
    top_n: int,
) -> tuple[str, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    for issue in symbol_issues:
        issue_type = "symbol_parse_error" if "error" in issue else "symbol_mismatch"
        issues.append({
            "type": issue_type,
            "severity": _severity(issue_type),
            "data": issue,
        })

    for name, files in duplicate_src_files.items():
        issues.append({
            "type": "duplicate_src_filename",
            "severity": _severity("duplicate_src_filename"),
            "data": {"name": name, "files": files},
        })

    for name, files in duplicate_test_files.items():
        issues.append({
            "type": "duplicate_test_filename",
            "severity": _severity("duplicate_test_filename"),
            "data": {"name": name, "files": files},
        })

    for name, files in duplicate_test_names.items():
        issues.append({
            "type": "duplicate_test_name",
            "severity": _severity("duplicate_test_name"),
            "data": {"name": name, "files": files},
        })

    for issue in nesting_issues:
        issue_type = issue.get("type") or "nesting_issue"
        issues.append({
            "type": issue_type,
            "severity": _severity(issue_type),
            "data": issue,
        })

    top_issues = sorted(issues, key=lambda x: x.get("severity", 0), reverse=True)[: max(1, int(top_n))]
    summary_rel = summary_path.strip().replace("\\", "/") if summary_path else "test-results/layout_validation_summary.json"
    summary_file = (root / summary_rel).resolve()
    if not str(summary_file).startswith(str(root)):
        summary_file = (root / "test-results" / "layout_validation_summary.json").resolve()
        summary_rel = "test-results/layout_validation_summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_doc = {
        "counts": counts,
        "top_issues": top_issues,
    }
    summary_file.write_text(json.dumps(summary_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_rel, top_issues


def _validate_project_layout(
    expected_symbols: str | list[str] | None = None,
    include_success_logs: bool = True,
    symbol_check_mode: str | None = None,
    symbol_check_whitelist: str | list[str] | None = None,
    summary_path: str | None = None,
    summary_top_n: int | None = None,
) -> str:
    root = _project_root()
    src_root = root / "src"
    tests_root = root / "tests"
    trace_lines: list[str] = []

    src_files = _collect_python_files(src_root)
    test_files = _collect_python_files(tests_root)

    if not src_files:
        _trace(trace_lines, "warning", "No Python files found under src/.")
    else:
        _trace(trace_lines, "info", f"Scanned {len(src_files)} source files under src/.")
    if not test_files:
        _trace(trace_lines, "warning", "No Python files found under tests/.")
    else:
        _trace(trace_lines, "info", f"Scanned {len(test_files)} test files under tests/.")

    expected: list[str] | None
    if isinstance(expected_symbols, str):
        expected = [x.strip() for x in expected_symbols.split(",") if x.strip()]
    elif isinstance(expected_symbols, list):
        expected = [str(x).strip() for x in expected_symbols if str(x).strip()]
    else:
        expected = None

    defaults = _layout_validation_defaults()
    resolved_symbol_mode = symbol_check_mode if symbol_check_mode is not None else str(defaults["symbol_check_mode"])
    resolved_whitelist_cfg = symbol_check_whitelist if symbol_check_whitelist is not None else defaults["symbol_check_whitelist"]
    resolved_summary_path = summary_path if summary_path is not None else str(defaults["summary_path"])
    resolved_summary_top_n = summary_top_n if summary_top_n is not None else int(defaults["summary_top_n"])

    mode = (resolved_symbol_mode or "strict").strip().lower()
    if mode not in {"strict", "relaxed", "off"}:
        mode = "strict"

    whitelist: list[str]
    if isinstance(resolved_whitelist_cfg, str):
        whitelist = [x.strip() for x in resolved_whitelist_cfg.split(",") if x.strip()]
    elif isinstance(resolved_whitelist_cfg, list):
        whitelist = [str(x).strip() for x in resolved_whitelist_cfg if str(x).strip()]
    else:
        whitelist = []

    symbol_checks = 0
    symbol_issues: list[dict[str, Any]] = []
    if mode == "off":
        _trace(trace_lines, "info", "Symbol check is disabled (mode=off).")
    else:
        for src_file in src_files:
            rel = src_file.relative_to(root).as_posix()
            if _is_whitelisted(rel, whitelist):
                if include_success_logs:
                    _trace(trace_lines, "info", f"Symbol check skipped by whitelist: {rel}")
                continue

            check = _check_main_symbols(src_file, expected)
            symbol_checks += 1
            matched = check.get("matched") or []
            present = check.get("present") or []
            has_non_private_symbol = any(not str(name).startswith("_") for name in present)

            check_ok = bool(check.get("ok"))
            if mode == "relaxed" and not check_ok:
                check_ok = has_non_private_symbol

            if check_ok:
                if include_success_logs:
                    if matched:
                        _trace(trace_lines, "info", f"Main symbol check passed for {rel}: {', '.join(matched)}")
                    elif mode == "relaxed":
                        _trace(trace_lines, "info", f"Main symbol check relaxed-pass for {rel}: found public symbols {present}")
                continue

            if check.get("error"):
                symbol_issues.append({"file": rel, "error": check["error"]})
                _trace(trace_lines, "error", check["error"])
            else:
                issue = {
                    "file": rel,
                    "expected_any_of": check.get("candidates") or [],
                    "found": present,
                }
                symbol_issues.append(issue)
                _trace(
                    trace_lines,
                    "warning",
                    f"Main symbol mismatch for {rel}. Expected one of {issue['expected_any_of']}, found {issue['found']}",
                )

    def _find_duplicate_basenames(paths: list[Path]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for p in paths:
            rel = p.relative_to(root).as_posix()
            grouped.setdefault(p.name, []).append(rel)
        return {name: rels for name, rels in grouped.items() if len(rels) > 1}

    duplicate_src_files = _find_duplicate_basenames(src_files)
    duplicate_test_files = _find_duplicate_basenames(test_files)

    if duplicate_src_files:
        _trace(trace_lines, "warning", f"Duplicate source filenames found: {len(duplicate_src_files)}")
    elif include_success_logs:
        _trace(trace_lines, "info", "No duplicate source filenames.")

    if duplicate_test_files:
        _trace(trace_lines, "warning", f"Duplicate test filenames found: {len(duplicate_test_files)}")
    elif include_success_logs:
        _trace(trace_lines, "info", "No duplicate test filenames.")

    test_name_map = _collect_top_level_test_names(test_files)
    duplicate_test_names = {
        name: files for name, files in test_name_map.items() if len(files) > 1
    }
    if duplicate_test_names:
        _trace(trace_lines, "warning", f"Duplicate test function names found: {len(duplicate_test_names)}")
    elif include_success_logs:
        _trace(trace_lines, "info", "No duplicate test function names.")

    nesting_issues: list[dict[str, Any]] = []
    src_dirs = {
        p.relative_to(src_root).parent.as_posix().rstrip("/")
        for p in src_files
    }
    test_dirs = {
        p.relative_to(tests_root).parent.as_posix().rstrip("/")
        for p in test_files
    }
    src_dirs.discard("")
    test_dirs.discard("")
    missing_test_dirs = sorted(d for d in src_dirs if d not in test_dirs)
    for d in missing_test_dirs:
        nesting_issues.append({"type": "missing_test_dir", "dir": d})
        _trace(trace_lines, "warning", f"Missing mirrored tests directory: tests/{d}")

    test_paths_set = {p.relative_to(root).as_posix() for p in test_files}
    for src_file in src_files:
        rel_src = src_file.relative_to(src_root)
        test_name = f"test_{rel_src.name}"
        candidate_a = (tests_root / rel_src.parent / test_name).relative_to(root).as_posix()
        candidate_b = (tests_root / test_name).relative_to(root).as_posix()
        if candidate_a not in test_paths_set and candidate_b not in test_paths_set:
            issue = {
                "type": "missing_test_file",
                "src": (src_root / rel_src).relative_to(root).as_posix(),
                "expected_any_of": [candidate_a, candidate_b],
            }
            nesting_issues.append(issue)
            _trace(
                trace_lines,
                "warning",
                f"No mirrored test for {issue['src']}. Expected {issue['expected_any_of']}",
            )
        elif include_success_logs:
            _trace(trace_lines, "info", f"Mirrored test exists for {(src_root / rel_src).relative_to(root).as_posix()}")

    counts = {
        "src_files": len(src_files),
        "test_files": len(test_files),
        "symbol_checks": symbol_checks,
        "symbol_issues": len(symbol_issues),
        "duplicate_src_filenames": len(duplicate_src_files),
        "duplicate_test_filenames": len(duplicate_test_files),
        "duplicate_test_names": len(duplicate_test_names),
        "nesting_issues": len(nesting_issues),
    }
    summary_rel, top_issues = _write_top_issues_summary(
        root=root,
        summary_path=resolved_summary_path,
        counts=counts,
        symbol_issues=symbol_issues,
        duplicate_src_files=duplicate_src_files,
        duplicate_test_files=duplicate_test_files,
        duplicate_test_names=duplicate_test_names,
        nesting_issues=nesting_issues,
        top_n=resolved_summary_top_n,
    )

    summary = {
        "ok": not symbol_issues and not duplicate_src_files and not duplicate_test_files and not duplicate_test_names and not nesting_issues,
        "counts": counts,
        "symbol_check_mode": mode,
        "symbol_check_whitelist": whitelist,
        "symbol_issues": symbol_issues,
        "duplicate_src_filenames": duplicate_src_files,
        "duplicate_test_filenames": duplicate_test_files,
        "duplicate_test_names": duplicate_test_names,
        "nesting_issues": nesting_issues,
        "summary_file": summary_rel,
        "summary_top_issues": top_issues,
        "trace": trace_lines,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _analyze_self_model() -> str:
    from src.reflection.self_model_analyzer import get_report_text
    return get_report_text()


def _get_improvement_plan() -> str:
    from src.reflection.self_improvement_planner import get_improvement_plan_text
    return get_improvement_plan_text()


def _update_self_model(system: str, module_name: str, action: str = "add") -> str:
    """Update self_model.json: add or remove a module from a system."""
    from src.hitl.audit_log import audit
    path = _config_dir() / "self_model.json"
    if not path.exists():
        return "self_model.json not found."
    data = json.loads(path.read_text(encoding="utf-8"))
    systems = data.get("systems") or {}
    if system not in systems:
        systems[system] = []
    mods = systems[system]
    if action == "add":
        if module_name not in mods:
            mods.append(module_name)
            systems[system] = sorted(mods)
    elif action == "remove":
        if module_name in mods:
            mods.remove(module_name)
    else:
        return "action must be 'add' or 'remove'"
    data["systems"] = systems
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("update_self_model", {"system": system, "module": module_name, "action": action})
    return f"Updated self_model: {action} {system}.{module_name}"


def _apply_patch_with_approval(path: str, content: str, reason: str = "") -> str:
    """Write proposed patch to pending_patches for human review. Does not overwrite target.
    For automated evolution use evolution.safety.submit_candidate_patch → validate → accept."""
    from src.hitl.audit_log import audit
    from datetime import datetime, timezone
    pending_dir = _config_dir() / "pending_patches"
    pending_dir.mkdir(parents=True, exist_ok=True)
    safe_name = path.replace("/", "_").replace("\\", "_")[:80]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    patch_file = pending_dir / f"{ts}_{safe_name}.patch"
    patch_file.write_text(f"# Reason: {reason}\n# Target: {path}\n\n{content}", encoding="utf-8")
    audit("apply_patch_with_approval", {"path": path, "patch_file": str(patch_file.name), "reason": reason[:100]})
    try:
        from src.communication.telegram_alerts import send_autonomous_event
        send_autonomous_event(f"⚠️ [СРОЧНО] Патч предложен для одобрения: {path} — {patch_file.name}", urgent=True)
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError):
        pass
    return f"Patch saved to config/pending_patches/{patch_file.name}. Review and apply manually to {path} if approved."


def _generate_module_skeleton(
    system: str,
    module_name: str,
    module_type: str = "module",
    on_exists: str = "notify",
) -> str:
    """
    Create a new module from template. module_type: module | system | agent.
    Uses templates/<module_type>/ (e.g. module.py or system.py or agent.py).
    """
    from src.hitl.audit_log import audit
    root = _project_root()
    tpl_dir = _templates_dir() / module_type
    if not tpl_dir.exists():
        return f"Template templates/{module_type}/ not found. Use module, system, or agent."
    system_dir = root / "src" / system
    system_dir.mkdir(parents=True, exist_ok=True)
    requested_file = system_dir / f"{module_name}.py"
    target_file = requested_file
    if target_file.exists():
        if on_exists == "notify":
            logger.warning("generate_module_skeleton: file exists %s", target_file)
            return f"File already exists: src/{system}/{module_name}.py"
        if on_exists == "suffix_new":
            suffix_idx = 1
            while target_file.exists():
                suffix = "_new" if suffix_idx == 1 else f"_new{suffix_idx}"
                target_file = system_dir / f"{module_name}{suffix}.py"
                suffix_idx += 1
            logger.info(
                "generate_module_skeleton: target exists, using alternate path %s",
                target_file,
            )
        else:
            return "on_exists must be 'notify' or 'suffix_new'"
    # Pick template file: module.py, system.py, agent.py, or first .py
    tpl_name = f"{module_type}.py" if (tpl_dir / f"{module_type}.py").exists() else "module.py"
    tpl_file = tpl_dir / tpl_name
    if not tpl_file.exists():
        for f in tpl_dir.iterdir():
            if f.suffix == ".py":
                tpl_file = f
                break
    if not tpl_file or not tpl_file.is_file():
        return f"No .py file in templates/{module_type}/."
    content = tpl_file.read_text(encoding="utf-8")
    final_module_name = target_file.stem
    content = content.replace("{{system}}", system).replace("{{module}}", final_module_name)
    target_file.write_text(content, encoding="utf-8")
    target_rel = target_file.relative_to(root).as_posix()
    audit(
        "generate_module_skeleton",
        {
            "system": system,
            "module": final_module_name,
            "type": module_type,
            "requested_module": module_name,
            "target": target_rel,
            "on_exists": on_exists,
        },
    )
    logger.info("generate_module_skeleton: created %s from template %s", target_rel, module_type)
    return f"Created {target_rel} from template {module_type}. Add tests and run update_self_model to register."


def _list_pending_patches(emotion_aware: bool = True) -> str:
    """Список патчей в config/pending_patches. При emotion_aware=True порядок зависит от эмоций."""
    pending_dir = _config_dir() / "pending_patches"
    if not pending_dir.exists():
        return "Нет каталога config/pending_patches."
    files = [(f, f.stat()) for f in pending_dir.iterdir() if f.is_file()]
    if not files:
        return "Нет файлов в config/pending_patches."
    if not emotion_aware:
        files.sort(key=lambda x: x[1].st_mtime)
        lines = [f"  {f.name}" for f, _ in files]
        return "Патчи (по времени создания):\n" + "\n".join(lines)
    try:
        from src.personality.emotion_matrix import get_state
        state = get_state()
        anxiety = state.get("anxiety", 0)
        frustration = state.get("frustration", 0)
        curiosity = state.get("curiosity", 0)
        # Тревога/фрустрация → сначала мелкие (меньше риск)
        if anxiety >= 0.4 or frustration >= 0.4:
            files.sort(key=lambda x: x[1].st_size)
            reason = "anxiety/frustration high → smallest first"
        elif curiosity >= 0.4:
            files.sort(key=lambda x: -x[1].st_mtime)
            reason = "curiosity high → newest first"
        else:
            files.sort(key=lambda x: x[1].st_mtime)
            reason = "FIFO (oldest first)"
        lines = [f"  {f.name} ({s.st_size} B)" for f, s in files]
        return f"Патчи (эмоциональный порядок: {reason}):\n" + "\n".join(lines)
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError):
        files.sort(key=lambda x: x[1].st_mtime)
        return "Патчи (по времени):\n" + "\n".join(f"  {f.name}" for f, _ in files)


def register_self_model_tools() -> None:
    register(
        "analyze_self_model",
        tool_schema(
            "analyze_self_model",
            "Read self_model.json and capabilities_reference.json, compare them. Returns what is implemented, missing, and what can be improved. Use to understand own architecture and gaps.",
            {},
            required=[],
        ),
        _analyze_self_model,
    )
    register(
        "get_improvement_plan",
        tool_schema(
            "get_improvement_plan",
            "Get ranked improvement plan from self-model analysis. Use after analyze_self_model to decide what to add next.",
            {},
            required=[],
        ),
        _get_improvement_plan,
    )
    register(
        "update_self_model",
        tool_schema(
            "update_self_model",
            "Add or remove a module in self_model.json (so the agent's self-model stays accurate after adding new code).",
            {
                "system": {"type": "string", "description": "System name, e.g. learning, evolution"},
                "module_name": {"type": "string", "description": "Module name to add or remove"},
                "action": {"type": "string", "description": "add or remove"},
            },
            required=["system", "module_name"],
        ),
        _update_self_model,
    )
    register(
        "generate_module_skeleton",
        tool_schema(
            "generate_module_skeleton",
            "Create a new module file from template (templates/module/ or system/ or agent/). Then use write_file to fill, run_pytest to verify, update_self_model to register.",
            {
                "system": {"type": "string", "description": "Target system folder under src/, e.g. learning"},
                "module_name": {"type": "string", "description": "New module name (file will be <module_name>.py)"},
                "module_type": {"type": "string", "description": "Template type: module, system, or agent"},
                "on_exists": {
                    "type": "string",
                    "description": "If target file exists: notify (default) or suffix_new to create <name>_new.py",
                },
            },
            required=["system", "module_name"],
        ),
        _generate_module_skeleton,
    )
    register(
        "validate_project_layout",
        tool_schema(
            "validate_project_layout",
            "Validate project source/test layout: main symbol names, duplicate files/tests, mirrored test nesting under tests/, and trace logging of successful and failed checks.",
            {
                "expected_symbols": {
                    "type": "string",
                    "description": "Optional expected class/function names, comma-separated. If omitted, uses file-name-based candidates.",
                },
                "include_success_logs": {
                    "type": "boolean",
                    "description": "Include successful checks in trace output (default true).",
                },
                "symbol_check_mode": {
                    "type": "string",
                    "description": "Symbol check strictness: strict, relaxed, or off (default strict).",
                },
                "symbol_check_whitelist": {
                    "type": "string",
                    "description": "Optional whitelist for symbol-check (paths/patterns), comma-separated, e.g. 'src/communication/*,src/main.py'.",
                },
                "summary_path": {
                    "type": "string",
                    "description": "Where to write short machine-readable summary JSON (default test-results/layout_validation_summary.json).",
                },
                "summary_top_n": {
                    "type": "integer",
                    "description": "Number of prioritized issues in summary file (default 20).",
                },
            },
            required=[],
        ),
        lambda expected_symbols=None, include_success_logs=True, symbol_check_mode=None, symbol_check_whitelist=None, summary_path=None, summary_top_n=None: _validate_project_layout(
            expected_symbols=expected_symbols,
            include_success_logs=include_success_logs,
            symbol_check_mode=symbol_check_mode,
            symbol_check_whitelist=symbol_check_whitelist,
            summary_path=summary_path,
            summary_top_n=summary_top_n,
        ),
    )
    register(
        "apply_patch_with_approval",
        tool_schema(
            "apply_patch_with_approval",
            "Save a proposed patch (file path + content) to config/pending_patches/ for human review. Does not apply automatically; human can then apply or reject. For safe evolution.",
            {
                "path": {"type": "string", "description": "Target path relative to project root"},
                "content": {"type": "string", "description": "Proposed file content"},
                "reason": {"type": "string", "description": "Short reason for the change"},
            },
            required=["path", "content"],
        ),
        _apply_patch_with_approval,
    )
    register(
        "list_pending_patches",
        tool_schema(
            "list_pending_patches",
            "List patches in config/pending_patches in order. With emotion_aware=True (default), order depends on agent emotions: high anxiety/frustration → smallest first; high curiosity → newest first; else oldest first. Use to choose which patch to apply next.",
            {"emotion_aware": {"type": "boolean", "description": "Use emotion-based ordering (default true)"}},
            required=[],
        ),
        lambda emotion_aware=True: _list_pending_patches(emotion_aware=emotion_aware),
    )
