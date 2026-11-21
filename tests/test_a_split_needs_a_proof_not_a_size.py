"""Раскол требует доказательства, а не толщины файла; успех — след в коде.

2026-09-21. Цель самоправки выбиралась по числу строк: самый толстый модуль
первым. Шесть файлов в пределах 180 строк друг от друга — разрежешь первый,
корона переедет на второй. 139 из 141 самостоятельных целей — «прочитать себя»
или «разбить себя»; ни одна строка из десяти заявок на самоправку не пережила
двух суток. А успех цели проверялся фразой «в data/approval_inbox.jsonl
появилась новая заявка self_apply_lane.run» — критерий мерил бумажку, а
наблюдатель не мог проверить даже её: `self_apply_lane.run` читался как имя
файла, и вердикт был «missing» при любом исходе. Сделанное и несделанное
выглядели одинаково.

Требование оператора: прежде чем резать, доказать. Признаки предложил сам
агент (21.09 05:15); здесь проверяется, что они считаются верно и что
ложное доказательство не рождается.
"""
from __future__ import annotations

from pathlib import Path

from core.split_proof import index_workspace, proof_for
from core.success_check import observe_success_check


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _group(prefix: str, n: int, lines_each: int) -> str:
    """n функций, связанных цепочкой вызовов, по lines_each строк."""
    out = []
    for i in range(n):
        call = f"    {prefix}_{i + 1}(x)\n" if i + 1 < n else ""
        body = "".join(f"    a{j} = x\n" for j in range(lines_each - 2))
        out.append(f"def {prefix}_{i}(x):\n{call}{body}")
    return "\n\n".join(out) + "\n"


def test_the_subject_named_like_the_module_stays_home(tmp_path) -> None:
    """core/verifier_absence.py — ворота отсутствия; выносятся ДРУГИЕ ворота."""
    _write(tmp_path, "core/gate_absence.py",
           _group("absence", 5, 30) + "\n\n" + _group("offtopic", 4, 40))
    proof = proof_for("core/gate_absence.py", index_workspace(tmp_path))
    assert proof is not None
    assert all(name.startswith("offtopic") for name in proof.names), proof.names


def test_a_code_trace_holds_only_after_the_change(tmp_path) -> None:
    _write(tmp_path, "core/a.py", "def _parse(x):\n    return x\n")
    check = "undefined:_parse@core/a.py — копия убрана"
    assert observe_success_check(check, tmp_path)["verdict"] == "missing"
    _write(tmp_path, "core/a.py", "from core.b import _parse  # noqa: F401\n")
    assert observe_success_check(check, tmp_path)["verdict"] == "verified"
