"""
Versioning: track config versions, rollback. MVP: single version.
"""
from __future__ import annotations

_versions: list[dict] = []


def snapshot(config: dict) -> str:
    vid = f"v{len(_versions)}"
    v: dict[str, object] = {"id": vid, "config": dict(config)}
    _versions.append(v)
    return vid


def rollback(version_id: str) -> dict | None:
    for v in _versions:
        if v["id"] == version_id:
            return v.get("config")
    return None
