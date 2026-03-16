from __future__ import annotations

import json

from src.evolution import config_manager


def test_load_config_resolves_active_profile(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "agent.json").write_text(
        json.dumps(
            {
                "active_profile": "canary",
                "autonomy_limits": {
                    "fetch_cache_max_entries": 10,
                    "fetch_cache_ttl_sec": 0,
                },
                "profiles": {
                    "canary": {
                        "autonomy_limits": {
                            "fetch_cache_max_entries": 77,
                            "fetch_cache_ttl_sec": 15,
                        },
                        "tool_performance": {"warn_threshold_sec": 1.5},
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config_manager, "_config_dir", cfg_dir)

    loaded = config_manager.load_config()
    assert loaded.get("active_profile") == "canary"
    assert (loaded.get("autonomy_limits") or {}).get("fetch_cache_max_entries") == 77
    assert (loaded.get("autonomy_limits") or {}).get("fetch_cache_ttl_sec") == 15
    assert (loaded.get("tool_performance") or {}).get("warn_threshold_sec") == 1.5
