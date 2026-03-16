"""
Config manager: load/save agent config (prompts, tools, params). Versioned by evolution.
Backup before save; restore from template for self-healing.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_config_dir = Path(__file__).resolve().parent.parent.parent / "config"
_backups_dir = _config_dir / "backups"


def _merge_dict(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_profile(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return {}

    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return cfg

    active = (os.environ.get("AGENT_PROFILE") or cfg.get("active_profile") or "").strip()
    if not active or active not in profiles:
        return cfg

    base_cfg = {k: v for k, v in cfg.items() if k not in ("profiles", "active_profile")}
    prof_cfg = profiles.get(active)
    if not isinstance(prof_cfg, dict):
        return base_cfg

    resolved = _merge_dict(base_cfg, prof_cfg)
    resolved["active_profile"] = active
    return resolved


def load_config() -> dict:
    p = _config_dir / "agent.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        return _resolve_profile(raw)
    return {}


def save_config(cfg: dict) -> None:
    _config_dir.mkdir(parents=True, exist_ok=True)
    p = _config_dir / "agent.json"
    # Backup before overwrite
    if p.exists():
        _backups_dir.mkdir(parents=True, exist_ok=True)
        backup_name = f"agent.json.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        (_backups_dir / backup_name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    # Version snapshot before overwrite (for rollback). Log and skip on failure.
    try:
        from src.evolution.versioning import snapshot
        snapshot(dict(load_config()))
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError, TypeError) as e:
        logger.debug("Version snapshot before save skipped: %s", e)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_prompt_rules() -> str:
    return (load_config().get("prompt_rules") or "").strip()


def restore_from_template() -> bool:
    """Restore config from config/agent.json.template. Returns True if template existed."""
    tpl = _config_dir / "agent.json.template"
    if not tpl.exists():
        return False
    cfg = json.loads(tpl.read_text(encoding="utf-8"))
    save_config(cfg)
    return True
