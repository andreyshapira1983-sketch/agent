"""Отборщик шагает мимо критического раскола к следующему выполнимому.

Живой замер первого тика под грантом (2026-08-28 08:31Z, MIR-183): весь верх
бэклога — расколы переросших модулей, и НИ ОДИН не считался выполнимым,
потому что затвор отбора нёс ВТОРУЮ копию предпосылки, похороненной MIR-179
(«переросший раскол невозможен» — написано до появления инкрементального
расщепителя). Отборщик не мог шагнуть к кандидату №2, падал в запасной
`select_top`, а №1 — раскол критического органа — умирал у менеджера:
`no_grounded_target` при четырёх выполнимых расколах прямо за ним.

Красный свидетель: до починки первый тест краснел ровно этой цепью.
"""
from __future__ import annotations

import types
from pathlib import Path

import core.backlog_selector as bl
import core.self_build_producer as mod


def _split_cand(target_rel: str):
    return types.SimpleNamespace(
        target_path=f"split:{target_rel}",
        signal_source="oversized_module",
        evidence_ref=f"oversized_module:{target_rel}",
        problem_quote="module is oversized",
        proposed_change="split",
        proof_of_value="",
        expected_effect="",
        confidence=0.4,
    )


def _write_big_module(root: Path, rel: str, lines: int) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"x{i} = {i}\n" for i in range(lines))
    (root / rel).write_text(body, encoding="utf-8")


def test_selector_advances_past_a_critical_split(tmp_path: Path, monkeypatch):
    """№1 — раскол критического органа, №2 — законный переросший раскол.
    Отбор обязан отдать №2, а не умереть на №1 через запасной путь."""
    critical_rel = "core/loop.py"  # в списке критических органов
    workable_rel = "core/huge_mod.py"
    _write_big_module(tmp_path, critical_rel, mod._MAX_SPLIT_TARGET_LINES + 50)
    _write_big_module(tmp_path, workable_rel, mod._MAX_SPLIT_TARGET_LINES + 50)

    backlog = [_split_cand(critical_rel), _split_cand(workable_rel)]
    monkeypatch.setattr(bl, "load_backlog", lambda *a, **k: backlog)

    picked = mod._default_grounded_selector(tmp_path)()
    assert picked is backlog[1], (
        "отборщик снова упёрся в критический №1 вместо выполнимого №2"
    )


def test_an_oversized_split_is_actionable_now(tmp_path: Path):
    """Сама умершая предпосылка, прибитая по имени: переросший раскол
    ВЫПОЛНИМ — им занимается детерминированный расщепитель (MIR-179)."""
    rel = "core/huge_mod.py"
    _write_big_module(tmp_path, rel, mod._MAX_SPLIT_TARGET_LINES + 50)

    assert mod._grounded_candidate_actionable(_split_cand(rel), tmp_path) is True
