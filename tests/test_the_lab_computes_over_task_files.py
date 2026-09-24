"""Лаборатория считает по файлам задачи, а не агент в уме.

Замер 2026-09-19, внешний экзамен из тридцати задач: суммы 40–60 чисел агент
считал в уме и записывал неверные (411 вместо 8009, 10231 вместо 9912), а
дважды записал круглое 10000 и сам же признал, что не считал. Лаборатория
файлов задачи не видела: её cwd — пустая временная папка. `inputs` копирует
названные файлы рабочей папки в папку эксперимента.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.step_sanitizer import _sanitize_python_probe
from core.verifier import verify
from tools.python_probe import PythonProbeTool

_SUM = "print(sum(int(x) for x in open('data/numbers.txt').read().split()))"


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "numbers.txt").write_text("214\n-310\n967\n", encoding="utf-8")
    return tmp_path


def test_a_sum_over_a_task_file_is_computed(tmp_path: Path):
    ws = _workspace(tmp_path)
    out = PythonProbeTool(workspace_root=ws).run(code=_SUM, inputs=["data/numbers.txt"])
    assert out["exit_code"] == 0, out["stderr"]
    assert out["stdout"].strip() == "871"
    assert out["inputs"] == ["data/numbers.txt"]


def test_the_experiment_cannot_write_back_into_the_workspace(tmp_path: Path):
    ws = _workspace(tmp_path)
    code = "from pathlib import Path\nPath('data/numbers.txt').write_text('0')\nprint('ok')"
    PythonProbeTool(workspace_root=ws).run(code=code, inputs=["data/numbers.txt"])
    assert (ws / "data" / "numbers.txt").read_text(encoding="utf-8") == "214\n-310\n967\n"


@pytest.mark.parametrize("bad", ["../outside.txt", "data/../../x", "C:/Windows/win.ini", "missing.txt"])
def test_a_path_outside_the_workspace_or_missing_is_refused(tmp_path: Path, bad: str):
    ws = _workspace(tmp_path)
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="inputs"):
        PythonProbeTool(workspace_root=ws).run(code="print(1)", inputs=[bad])


def test_a_probe_without_a_workspace_refuses_inputs_by_name(tmp_path: Path):
    with pytest.raises(ValueError, match="workspace"):
        PythonProbeTool().run(code="print(1)", inputs=["x.txt"])


def test_a_probe_without_inputs_keeps_its_empty_lab(tmp_path: Path):
    ws = _workspace(tmp_path)
    out = PythonProbeTool(workspace_root=ws).run(code="import os\nprint(os.listdir('.'))")
    assert out["stdout"].strip() == "[]", "without inputs the lab must not see the workspace"


def test_the_doorman_passes_inputs_and_rejects_a_malformed_list():
    warnings: list[str] = []
    ok = _sanitize_python_probe({"code": _SUM, "inputs": ["data/numbers.txt"]}, 0, warnings)
    assert ok is not None and ok["arguments"]["inputs"] == ["data/numbers.txt"]
    assert _sanitize_python_probe({"code": _SUM, "inputs": "data/numbers.txt"}, 1, warnings) is None
    assert any("inputs must be a list" in w for w in warnings)


def test_the_default_agent_gives_the_lab_its_workspace():
    from app import bootstrap

    src = Path(bootstrap.__file__).read_text(encoding="utf-8")
    assert "PythonProbeTool(workspace_root=workspace)" in src


def test_a_task_file_named_but_not_given_is_copied_by_itself(tmp_path: Path):
    """Замер 2026-09-19: без входного файла эксперимент напечатал «exists: False»,
    и строка ушла в sum.txt как результат; позже, в опыте с библиотекой книг,
    планировщик снова забыл `inputs`, и три попытки сгорели на FileNotFoundError.
    Файл рабочей папки, названный в коде, лаборатория копирует сама."""
    ws = _workspace(tmp_path)
    code = "import os\nprint('exists:', os.path.exists('data/numbers.txt'))"
    out = PythonProbeTool(workspace_root=ws).run(code=code)
    assert out["stdout"].strip() == "exists: True"
    assert out["auto_inputs"] == ["data/numbers.txt"] and out["missing_inputs"] == []


def test_a_file_over_the_limit_is_still_named_as_missing(tmp_path: Path, monkeypatch):
    """Что не влезло — названо, и результатом не считается.

    Проверяется ПРАВИЛО, а не число: 2026-09-20 потолок входа поднят с 2 МБ до
    16 МБ, чтобы агент мог считать по собственным журналам, и прежние 3 МБ
    перестали быть «слишком большими».
    """
    import tools.python_probe as probe_mod

    monkeypatch.setattr(probe_mod, "_INPUT_MAX_BYTES", 1024 * 1024)
    ws = _workspace(tmp_path)
    (ws / "big.txt").write_bytes(b"x" * (3 * 1024 * 1024))
    out = PythonProbeTool(workspace_root=ws).run(code="print(len(open('big.txt').read()))")
    assert out["missing_inputs"] == ["big.txt"]
    assert "add them to inputs" in out["note"]


def test_the_lab_never_copies_a_credential_file(tmp_path: Path):
    """Тот же запрет, что у file_read: .env не попадает в эксперимент ни явно, ни сам."""
    ws = _workspace(tmp_path)
    (ws / ".env").write_text("OPENAI_API_KEY=sk-test", encoding="utf-8")
    lab = PythonProbeTool(workspace_root=ws)
    out = lab.run(code="import os\nprint(os.path.exists('.env'))")
    assert out["stdout"].strip() == "False"
    with pytest.raises(ValueError, match="credential"):
        lab.run(code="print(1)", inputs=[".env"])


def _probe_chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="python_probe",
        arguments={"code": "import csv\nprint('{}')", "inputs": ["data.csv"]},
        output={"code": "import csv\nprint('{}')", "inputs": ["data.csv"], "missing_inputs": [],
                "exit_code": 0, "stdout": '{"alice": 1.0}\n', "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False,
                "duration_ms": 156, "timed_out": False},
    ))
    return chain


def test_true_facts_of_a_run_are_not_refuted() -> None:
    """Замер 2026-09-19: «скрипт прочитал data.csv, missing_inputs пуст» получали
    [claim-refuted] — полей исхода не было в улике, и верный эпизод терял опыт."""
    answer = (
        "Conclusion: суммы посчитаны [tool:python_probe]\n"
        "Facts:\n"
        "- Скрипт прочитал `data.csv` и просуммировал `amount`; код завершился с "
        "`exit_code: 0` [tool:python_probe].\n"
        "- Все входные данные были на месте (`missing_inputs: []`), вывод не усечён "
        "(`stdout_truncated: false`) [tool:python_probe].\n"
        "Sources:\n1. tool:python_probe - probe\nConfidence: high\nUnverified: nothing\n"
    )
    report = verify(answer=answer, chain=_probe_chain(), user_question="суммы по name")
    assert report.refuted_chunks == 0, [(c.verdict, getattr(c.reason, "code", None))
                                        for c in report.chunks]


def test_the_experiment_code_still_stays_out_of_the_evidence() -> None:
    """Прежний договор цел: код — вопрос, а не ответ мира.

    2026-09-20 договор уточнён: код лежит за маркером `QUESTION-CODE` и не
    участвует в суждении об истине (`truth_excerpt`), зато имена, которые ход
    сам написал, перестали считаться выдуманными.
    """
    from core.evidence import QUESTION_CODE_MARKER
    from core.verifier_utils import truth_excerpt

    excerpt = _probe_chain().evidences[0].excerpt
    outcome = truth_excerpt(excerpt)
    assert "import csv" not in outcome
    assert "inputs: [\"data.csv\"]" in outcome
    assert "import csv" in excerpt.split(QUESTION_CODE_MARKER, 1)[1]


@pytest.mark.parametrize("claim", [
    "- Скрипт завершился с кодом 0, ошибок нет, входной файл `data.csv` найден [tool:python_probe].",
    ("- Скрипт прочитал `data.csv` через `csv.DictReader`, просуммировал `amount` по `name`; "
     "код завершился с `exit_code: 0`, ошибок в stderr нет [tool:python_probe]."),
    ("- Код завершился с `exit_code: 0`, ошибок в `stderr` нет, длительность 233 мс "
     "[tool:python_probe]."),
])
def test_no_errors_does_not_make_the_read_file_absent(claim: str) -> None:
    """Замер 2026-09-19: «ошибок нет» делало весь кусок утверждением об отсутствии,
    предметом становился `data.csv` из соседней части, улика его содержала — и
    верное утверждение получало absence_refuted_by_evidence."""
    answer = ("Facts:\n" + claim + "\nSources:\n1. tool:python_probe - p\n"
              "Confidence: high\nUnverified: nothing\n")
    report = verify(answer=answer, chain=_probe_chain(), user_question="суммы по name")
    assert report.refuted_chunks == 0, [(c.verdict, getattr(c.reason, "code", None))
                                        for c in report.chunks]


def test_an_absence_beside_a_presence_is_still_judged() -> None:
    """ПРЕДОХРАНИТЕЛЬ: часть с «нет» судится как раньше, и её предмет — свой."""
    from core.verifier_absence import absence_refuted_by_excerpt

    claim = "Файл `data.csv` прочитан, но поля `amount_total` в нём нет"
    assert absence_refuted_by_excerpt(claim, "amount_total = 5")
    assert not absence_refuted_by_excerpt(claim, "inputs: [\"data.csv\"]")
    # Место поиска существует; отсутствует искомое — и оно по-прежнему судится.
    place = "В `report.py` нет функции `render_total`"
    assert absence_refuted_by_excerpt(place, "def render_total(): ...")
    assert not absence_refuted_by_excerpt(place, "report.py: 12 lines")


def test_cyrillic_output_arrives_intact(tmp_path: Path):
    """Замер 2026-09-19 (рабочий экзамен): «'product': '���'» — вывод лаборатории
    на Windows шёл в кодировке консоли, и агент выдумал названия товаров."""
    ws = _workspace(tmp_path)
    (ws / "sales.csv").write_text("product\nмёд\nчай\n", encoding="utf-8")
    code = "print(open('sales.csv', encoding='utf-8').read().split()[1:])"
    out = PythonProbeTool(workspace_root=ws).run(code=code, inputs=["sales.csv"])
    assert out["stdout"].strip() == "['мёд', 'чай']", out
