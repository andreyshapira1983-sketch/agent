"""Совпадение по слову должно стоить по-разному, иначе выбор решает шум.

Background: docs/CODE_NOTES.md, "Resolving power".
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import ProceduralMemoryStore, ProcedureRecord
from core.topic_tokens import FLAT, build_salience, topic_tokens

#: Речь оператора: служебные слова в каждой реплике, предмет — в одной.
_SPEECH = [
    "а это что такое и где оно",
    "это где-то там, посмотри",
    "где это лежит, покажи",
    "а это точно так работает?",
    "найди, где рождается evidence_budget_trim",
]


def test_an_ordinary_word_weighs_less_than_a_rare_one():
    """Ядро правки. «это» и «где» звучат в каждой реплике, имя сигнала — в
    одной, и совпадение по ним не может стоить одинаково.
    """
    salience = build_salience(_SPEECH)

    assert salience.weight("evidence_budget_trim") > salience.weight("это")
    assert salience.weight("evidence_budget_trim") > salience.weight("где")


def test_a_word_never_spoken_resolves_most():
    """Слово, которого в корпусе нет, пришло с этой задачей — сильнее него не
    разрешает ничто из известного.
    """
    salience = build_salience(_SPEECH)

    assert salience.weight("citation_fabricated") >= max(salience.weights.values())


def test_a_corpus_of_one_does_not_zero_everything():
    """Живая поломка при первой сборке: `log(1/1)` обнулял КАЖДОЕ слово, и
    подбор переставал находить хоть что-то. Ноль — это не «мало значит», это
    «не существует».
    """
    salience = build_salience(["это где все"])

    assert salience.weight("это") > 0
    assert salience.weight("невиданное") > 0


def test_no_corpus_means_the_old_rules():
    """Пустая память не вправе менять правила, по которым её читают: без
    корпуса вес плоский и счёт штучный, как до правки.
    """
    assert build_salience([]) is FLAT
    assert FLAT.weight("что угодно") == 1.0


def test_a_compound_name_is_kept_whole_as_well_as_split():
    """Части ловят родство, целое ловит предмет. Без целого запись со словом
    `evidence` неотличима от записи про сам сигнал.
    """
    tokens = topic_tokens("сигнал evidence_budget_trim в core/loop.py")

    assert {"evidence", "budget", "trim"} <= tokens
    assert "evidence_budget_trim" in tokens
    assert "loop.py" in tokens


def _proc(name: str, tags: tuple[str, ...], status: str = "candidate") -> ProcedureRecord:
    return ProcedureRecord(
        name=name,
        workflow_key="tools:list_dir->shell_exec",
        trigger_tags=tags,
        steps=(f"Situation: {name}", "Run tool: shell_exec"),
        status=status,
    )


def test_one_rare_name_outweighs_three_ordinary_words(tmp_path: Path):
    """Замерено живьём 2026-08-15: победителя выбирали «все», «где», «это».
    Три служебных слова обходили запись про тот же самый сигнал.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    # Служебных слов намеренно БОЛЬШЕ, чем частей у имени: составное имя даёт
    # четыре токена (три части и целое), и при штучном счёте пять служебных
    # слов их перевешивают. Ровно та арифметика, что победила на живом
    # хранилище, — только там пятое слово нашлось само.
    store.rewrite([
        _proc("прочее", ("это", "где", "все", "найди", "покажи")),
        _proc("про сигнал", ("evidence_budget_trim",)),
    ])
    question = "а это где все — найди и покажи evidence_budget_trim"

    by_count = store.search_with_report(question, salience=FLAT).procedures
    by_weight = store.search_with_report(
        question, salience=build_salience(_SPEECH),
    ).procedures

    assert by_count[0].name == "прочее", "штучный счёт вёл себя иначе, чем замерено"
    assert by_weight[0].name == "про сигнал"


def test_maturity_breaks_a_tie_but_does_not_override_the_subject(tmp_path: Path):
    """Затвор зрелости охранял «неподтверждённое не вытесняет подтверждённое»,
    и при РАВНОЙ уместности это в силе. Поверх темы он давал другое: на живом
    хранилище единственная доказанная запись забирала первое место при любом
    совпадении, и это стоило 24 пункта точности.
    """
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite([
        _proc("доказанное про другое", ("это",), status="active"),
        _proc("кандидат про предмет", ("evidence_budget_trim",)),
    ])
    salience = build_salience(_SPEECH)

    on_subject = store.search_with_report(
        "это evidence_budget_trim", salience=salience,
    ).procedures
    assert on_subject[0].name == "кандидат про предмет"

    store.rewrite([
        _proc("доказанное", ("evidence_budget_trim",), status="active"),
        _proc("кандидат", ("evidence_budget_trim",)),
    ])
    tied = store.search_with_report("evidence_budget_trim", salience=salience).procedures
    assert tied[0].status == "active", "при равной уместности зрелость обязана решать"
