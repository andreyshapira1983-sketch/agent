"""«Нулевая уверенность» — приговор должнику улик, а не тому, кто их не должен.

ЗАМЕР 2026-08-13, трасса trace_da0f132bd8e203a77ae5870fdbb39a90. Светская
реплика («скажи что-нибудь умное») прошла весь конвейер: `evidence_support`
вынес вердикт `applicable=False, reason=no_evidence_expected` — этот ход улик
не был должен. А хвост сводки в том же ходе сказал оператору «уверенность:
нулевая»: `_confidence_wording(0, 5)` вердикта не читал. Два узла одного хода
разошлись во мнении о том, были ли улики нужны, — ровно та развилка, ради
которой `core/evidence_support.py` переписывали (его же docstring предупреждал:
«callers that need that distinction must read evaluate_evidence_support»).

Эти тесты закрепляют: вердикт применимости доходит до композитора сводки, и
хвост «улики не требовались» отличим от хвоста «улики требовались и их нет».
"""
from __future__ import annotations


def test_the_loop_actually_hands_the_verdict_to_the_composer() -> None:
    """Проводка: параметр существует, потому что его кто-то передаёт.

    Урок 2026-08-09 (`test_confidence_vector_reaches_the_operator`): узел,
    чей результат только журналируется, для оператора не существует. Тот же
    режим отказа здесь ловится проверкой места вызова.
    """
    import inspect

    import core.loop_response_deciders
    src = inspect.getsource(core.loop_response_deciders)
    assert "evidence_support=" in src, (
        "build_verification_summary вызывается без вердикта применимости — "
        "хвост снова начнёт объявлять нулевую уверенность там, где улики "
        "не требовались"
    )
