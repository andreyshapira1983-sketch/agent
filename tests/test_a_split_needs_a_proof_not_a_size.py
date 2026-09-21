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

_BODY = "\n".join(f"    v{i} = x + {i}" for i in range(8)) + "\n    return v0\n"


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


def test_a_verbatim_copy_is_a_dup_proof(tmp_path) -> None:
    fn = f"def _parse(x):\n{_BODY}\n\ndef _other(x):\n{_BODY}"
    _write(tmp_path, "core/a.py", fn)
    _write(tmp_path, "core/b.py", fn.replace("_parse", "_parse_iso").replace("_other", "_o2"))
    proof = proof_for("core/a.py", index_workspace(tmp_path))
    assert proof is not None and proof.kind == "dup" and proof.other == "core/b.py"


def test_a_fat_file_with_one_subject_has_no_proof(tmp_path) -> None:
    _write(tmp_path, "core/fat.py", "x = 1\n" * 3000 + _group("step", 10, 30))
    assert proof_for("core/fat.py", index_workspace(tmp_path)) is None


def test_two_disconnected_subjects_are_a_proof(tmp_path) -> None:
    _write(tmp_path, "core/mixed.py", _group("alpha", 5, 30) + "\n\n" + _group("beta", 5, 30))
    proof = proof_for("core/mixed.py", index_workspace(tmp_path))
    assert proof is not None and proof.kind == "multi_subject"
    assert len(proof.groups) == 2


def test_the_subject_named_like_the_module_stays_home(tmp_path) -> None:
    """core/verifier_absence.py — ворота отсутствия; выносятся ДРУГИЕ ворота."""
    _write(tmp_path, "core/gate_absence.py",
           _group("absence", 5, 30) + "\n\n" + _group("offtopic", 4, 40))
    proof = proof_for("core/gate_absence.py", index_workspace(tmp_path))
    assert proof is not None
    assert all(name.startswith("offtopic") for name in proof.names), proof.names


def test_a_module_with_one_client_is_not_a_proof(tmp_path) -> None:
    """Правило «чужого дома» по одному клиенту гнало агента отменять раскол
    core/loop.py (110 ложных срабатываний на живом коде) — его здесь нет."""
    _write(tmp_path, "core/loop_part.py", _group("part", 6, 40))
    _write(tmp_path, "core/loop.py", "from core.loop_part import part_0\n\npart_0(1)\n")
    assert proof_for("core/loop_part.py", index_workspace(tmp_path)) is None


def test_a_code_trace_holds_only_after_the_change(tmp_path) -> None:
    _write(tmp_path, "core/a.py", "def _parse(x):\n    return x\n")
    check = "undefined:_parse@core/a.py — копия убрана"
    assert observe_success_check(check, tmp_path)["verdict"] == "missing"
    _write(tmp_path, "core/a.py", "from core.b import _parse  # noqa: F401\n")
    assert observe_success_check(check, tmp_path)["verdict"] == "verified"


def test_the_old_paperwork_check_proved_nothing(tmp_path) -> None:
    """Прежний критерий не отличал сделанное от несделанного — вот почему он ушёл.

    Вердикт одинаков, подана заявка или нет: `self_apply_lane.run` читается как
    имя файла, которого нет, — «missing» при любом исходе.
    """
    old = "в data/approval_inbox.jsonl появилась новая заявка self_apply_lane.run"
    _write(tmp_path, "data/approval_inbox.jsonl", "")
    before = observe_success_check(old, tmp_path)["verdict"]
    _write(tmp_path, "data/approval_inbox.jsonl",
           '{"payload": {"operation": "self_apply_lane.run", "status": "pending"}}\n')
    after = observe_success_check(old, tmp_path)["verdict"]
    assert before == after, "критерий, не видящий разницы, ничего не проверяет"
