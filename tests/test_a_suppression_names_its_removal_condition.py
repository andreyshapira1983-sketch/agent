"""Затычка обязана объявить себя временной или постоянной — молча нельзя.

Замер, внешний класс и границы: MIR-181 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Гориллья затычка Google стоит 8+ лет, потому что «временно» нигде не было
записано машинно-читаемо и никто не встречал просрочку. Внутренний прецедент
уже оплачен: причина отказа «нет расщепителя», пережившая расщепитель,
стоила 234 единицы за день (MIR-179).

Грамматика: `until: YYYY-MM-DD — <что сделать>` или `until: <событие>` —
временная; `standing: <почему постоянна>` — постоянная ПО ЗАМЫСЛУ. Просроченная
дата краснит батарею: читатель просроченного — сама батарея, потому что только
её никто не может не заметить. Бухнуть дату вперёд — легально, но это явный
акт в git, а не тихое старение.

Вне охвата (решение, не пропуск): храповик ruff на 113 — сам себе читатель
(любой рост краснит `test_ruff_config`, давление только вниз); флейковый
таймерный тест H-10 — открытый пункт оператора, подавления в коде у него нет.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TESTS = Path(__file__).resolve().parent

_MARKER = re.compile(r"\b(until|standing):")
_DATED = re.compile(r"until:\s*(\d{4}-\d{2}-\d{2})")


def _test_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8"))
            for p in sorted(_TESTS.glob("test_*.py"))]


def test_every_xfail_names_its_review_condition() -> None:
    """Красный свидетель: 13 xfail несут причину, ноль — условие пересмотра."""
    naked: list[str] = []
    for path, src in _test_sources():
        if path.name == Path(__file__).name:
            continue
        markers = src.count("pytest.mark.xfail")
        if not markers:
            continue
        conditions = len(_MARKER.findall(src))
        if conditions < markers:
            naked.append(f"{path.name}: {markers} xfail, {conditions} условий")
    assert not naked, "xfail без until:/standing: — молчаливая временность:\n" + "\n".join(naked)


def test_the_orphan_allowlist_entries_declare_their_kind() -> None:
    """Каждая запись allowlist — либо until:, либо standing:, в самой записи."""
    src = (_ROOT / "scripts" / "architecture_invariants.py").read_text(encoding="utf-8")
    allowlist = None
    for node in ast.parse(src).body:
        target = getattr(node, "target", None) or (
            node.targets[0] if getattr(node, "targets", None) else None)
        if getattr(target, "id", "") == "_ORPHAN_ALLOWLIST":
            allowlist = ast.literal_eval(node.value)
    assert allowlist is not None, "_ORPHAN_ALLOWLIST не найден"
    naked = [k for k, v in allowlist.items() if not _MARKER.search(str(v))]
    assert not naked, f"записи allowlist без until:/standing:: {naked}"


def test_the_scan_exemption_block_declares_both_kinds() -> None:
    """Освобождения от сканирования: рамочные — standing, read_logs — until.

    `read_logs` освобождён НАМЕРЕННО (аудит §5), но условие снятия жило в
    аудите, а не при затычке — никто бы его не перепроверил.
    """
    src = (_ROOT / "core" / "loop_step_execution.py").read_text(encoding="utf-8")
    head = src[: src.index("_INJECTION_SCAN_EXEMPT")]
    block = head[head.rfind("\n#") - 2000:] + src[src.index("_INJECTION_SCAN_EXEMPT"):
                                                  src.index("_INJECTION_SCAN_EXEMPT") + 500]
    assert "standing:" in block, "рамочные освобождения не объявлены постоянными"
    assert "until:" in block, "у read_logs нет условия снятия при самой затычке"


def test_an_expired_review_date_reddens_the_battery() -> None:
    """Читатель просроченного: дата в прошлом — красная батарея, не тишина."""
    today = datetime.now(timezone.utc).date().isoformat()
    expired: list[str] = []
    surfaces = [p for p, _ in _test_sources() if p.name != Path(__file__).name]
    surfaces += [_ROOT / "scripts" / "architecture_invariants.py",
                 _ROOT / "core" / "loop_step_execution.py"]
    for path in surfaces:
        for found in _DATED.findall(path.read_text(encoding="utf-8")):
            if found < today:
                expired.append(f"{path.name}: until: {found}")
    assert not expired, (
        "срок пересмотра истёк — перемерь и почини, или пере-датируй явным "
        "коммитом:\n" + "\n".join(expired))
