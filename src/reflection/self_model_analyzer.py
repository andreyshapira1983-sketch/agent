"""
Self-Model analyzer: compare self_model.json with capabilities_reference.json.
Returns what is implemented, missing, and what can be improved.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

_config_dir = Path(__file__).resolve().parent.parent.parent / "config"


def load_self_model() -> dict[str, Any]:
    p = _config_dir / "self_model.json"
    if not p.exists():
        return {}
    return cast(dict[str, Any], json.loads(p.read_text(encoding="utf-8")))


def load_capabilities_reference() -> dict[str, Any]:
    p = _config_dir / "capabilities_reference.json"
    if not p.exists():
        return {}
    return cast(dict[str, Any], json.loads(p.read_text(encoding="utf-8")))


def analyze() -> dict[str, Any]:
    """
    Compare self_model with capabilities_reference.
    Returns: implemented, missing, can_improve, report_text.
    """
    self_model = load_self_model()
    ref = load_capabilities_reference()
    systems_self = self_model.get("systems") or {}
    tools_self = set(self_model.get("tools") or [])
    missing_flags = self_model.get("missing") or {}

    implemented: list[str] = []
    missing: list[str] = []
    can_improve: list[str] = []

    # Compare systems and modules (ref: system_name -> list of module names)
    for sys_name, ref_list in ref.items():
        if sys_name in ("description", "capability_levels") or not isinstance(ref_list, list):
            continue
        self_modules = systems_self.get(sys_name) or []
        self_set = set(self_modules)
        for m in ref_list:
            full = f"{sys_name}.{m}"
            if m in self_set:
                implemented.append(full)
            else:
                missing.append(full)

    # Missing flags from self_model
    for flag, is_missing in missing_flags.items():
        if is_missing:
            missing.append(f"capability.{flag}")

    # Can improve: things that exist but could be extended (from reference levels)
    levels = ref.get("capability_levels") or {}
    for level_name, desc in levels.items():
        if "self_model" in level_name or "improvement" in level_name:
            can_improve.append(f"{level_name}: {desc}")

    # Build report text
    lines = [
        "=== Self-Model Analysis ===",
        f"Implemented (sample): {len(implemented)} modules/systems.",
        f"Missing (sample): {len(missing)} items.",
        "",
        "Missing capabilities:",
    ]
    for m in missing[:25]:
        lines.append(f"  - {m}")
    if len(missing) > 25:
        lines.append(f"  ... and {len(missing) - 25} more")
    lines.append("")
    lines.append("Can improve (levels):")
    for c in can_improve[:10]:
        lines.append(f"  - {c}")
    lines.append("")
    lines.append(f"Tools registered: {len(tools_self)}")

    return {
        "implemented_count": len(implemented),
        "missing_count": len(missing),
        "implemented_sample": implemented[:20],
        "missing": missing[:30],
        "can_improve": can_improve,
        "tools_count": len(tools_self),
        "report_text": "\n".join(lines),
    }


def get_report_text() -> str:
    return cast(str, analyze()["report_text"])
