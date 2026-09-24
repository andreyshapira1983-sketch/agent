"""Карта говорит, где что подключается за пределами core/, — и не врёт.

Ночь 24→25.09 (trace_a4863d22…): агент искал tools/registry.py, `def
build_registry`, `TOOLS =` и писал `Risk.REVERSIBLE` — угадывал устройство;
место регистрации инструментов прочитал после шести заходов. Карта описывала
только core/. Каждое её утверждение здесь сверяется с кодом.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


def test_the_map_names_the_wiring_places() -> None:
    text = _read("knowledge/generated/AGENT_ANATOMY.md")
    for place in ("app/bootstrap.py", "tools/base.py", "for_role(", "proposals/selffix/",
                  "| `tools/spawn_subagent` |"):
        assert place in text, f"карта не называет: {place}"


def test_every_wiring_claim_holds_in_the_code() -> None:
    assert "registry.register(" in _read("app/bootstrap.py")
    assert "there is no separate registry module" in _read("knowledge/generated/AGENT_ANATOMY.md")
    assert not (_REPO / "tools" / "registry.py").exists()
    base = _read("tools/base.py")
    assert "Risk = Literal[" in base and "class ToolRegistry" in base
    router = _read("core/model_router.py")
    assert "def for_role(" in router and "class ModelRole" in router
    assert "model_router=model_router" in _read("app/bootstrap.py")
