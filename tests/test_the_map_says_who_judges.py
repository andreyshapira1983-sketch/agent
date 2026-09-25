"""Карта устройства говорит, кто судит ответ и в каком порядке (сведение слоёв, 25.09).

Судей тринадцать, и до этого раздела их порядок знал только код. Раздел
генерируется, поэтому проверяется то, что может разойтись с кодом: каждый
названный судья существует в core/ и вызывается из хода или прогона.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _gen():
    spec = importlib.util.spec_from_file_location("gen_anatomy", ROOT / "scripts" / "gen_anatomy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_named_judge_is_a_live_module_somebody_calls() -> None:
    gen = _gen()
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "core").glob("*.py"))
    for _stage, mod, _how, _what in gen.JUDGES:
        assert (ROOT / "core" / f"{mod}.py").is_file(), mod
        # Кто-то вне самого модуля его импортирует: мёртвый судья в карте — ложь.
        importers = [p for p in (ROOT / "core").glob("*.py")
                     if p.stem != mod and any(f"{prefix}{mod} import" in p.read_text(encoding="utf-8")
                                              for prefix in ("core.", "from ."))]
        assert importers, f"no core module imports core.{mod}"
    assert "core.judge_queue" in sources


def test_the_generated_map_carries_the_section_in_order() -> None:
    gen = _gen()
    doc = gen.build_document()
    assert "## Who judges an answer, and in what order" in doc
    positions = [doc.index(f"`core/{mod}`", doc.index("## Who judges")) for _s, mod, _h, _w in gen.JUDGES]
    assert positions == sorted(positions)
    committed = (ROOT / "knowledge" / "generated" / "AGENT_ANATOMY.md").read_text(encoding="utf-8")
    assert "## Who judges an answer, and in what order" in committed
