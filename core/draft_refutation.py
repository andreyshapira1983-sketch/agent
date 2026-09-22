"""Черновик, который противоречит своим же уликам, переписывается один раз.

Разговор 2026-09-22, 11:52: агент написал «пробой это подтверждается:
verified», а все три его пробы упали с TypeError (exit_code=1). Проверяющий
пометил фразу [claim-refuted] — и она ушла человеку первой строкой. Причина
в устройстве: опровержение клалось в историю провалов для СЛЕДУЮЩЕЙ попытки
(`_replan_on_refuted_claims`), а после проверки следующей попытки нет —
черновик уходил как был, только с пометкой.

Здесь — проход Chain-of-Verification (Dhuliawala et al., 2023: черновик →
проверка → исправленный ответ) и RARR (Gao et al., 2023: исследовать и
переписать): до ответа черновик сверяется с той же цепочкой улик тем же
детерминированным проверяющим; если опровергнутое есть, синтезатор один раз
собирает ответ заново по ТЕМ ЖЕ уликам, получив дословно, что именно
опровергнуто и чем. Остаётся черновик с меньшим числом опровержений.

Удалять опровергнутые фразы механически нельзя: 2026-09-21 из пяти
проверенных руками «опровержений» все пять оказались правдой (класс
«слов нет в вырезке», с тех пор помечается отдельно). Поэтому решает модель
по уликам, а итог снова проходит обычную проверку.
"""
from __future__ import annotations

import re
from typing import Any

_MAX_LISTED = 5


def _refuted(report: Any) -> list[Any]:
    return [c for c in getattr(report, "chunks", ()) or () if getattr(c, "reason", None)]


_GREEN_CLAIM = re.compile(r"(verdict|вердикт)[^\n]{0,25}(green|зелён)|доведен\w*\s+до\s+зел[её]н",
                          re.IGNORECASE)


def _outcome_contradictions(st: Any, answer: str) -> list[Any]:
    """Заявленный зелёный вердикт при красном последнем patch_check.

    2026-09-22 16:03: агент написал «verdict: green, full_exit_code: 0», а
    единственный patch_check хода был red; проверяющий засчитал «green», потому
    что в выводе стояло «tests are not green» — совпадение слова, не смысла.
    """
    from types import SimpleNamespace

    checks = [a for a in (getattr(st, "artifacts", None) or {}).values()
              if (a or {}).get("tool") == "patch_check" and isinstance((a or {}).get("output"), dict)]
    if not checks:
        return []
    last = checks[-1]["output"]
    if last.get("verdict") == "green" or not _GREEN_CLAIM.search(answer or ""):
        return []
    reason = SimpleNamespace(code="outcome_contradicts_tool", explanation="the last patch_check is NOT green",
                             computed_from="tool_output:patch_check",
                             actual=f"verdict={last.get('verdict')}, why={last.get('why')}, "
                                    f"tests_exit_code={last.get('tests_exit_code')}")
    return [SimpleNamespace(text=_GREEN_CLAIM.search(answer).group(0), reason=reason)]


def _check(loop: Any, st: Any, answer: str) -> Any:
    from core.verifier import verify

    return verify(
        answer=answer, chain=st.chain, user_question=st.user_question,
        expects_contract_headers=bool(getattr(loop, "_synthesis_expects_contract_headers", True)),
    )


def revise_refuted_draft(loop: Any, st: Any, do_synthesize: Any) -> None:
    """Сверить черновик до ответа и один раз переписать опровергнутое."""
    from core.replan import ReplanTrigger
    from core.synth_resilience import SynthAttempt

    if (st.chain is None or not getattr(loop, "verifier_enabled", False)
            or getattr(loop, "_last_synth_degraded", False) or not (st.draft_answer or "").strip()):
        return
    try:
        refuted = _outcome_contradictions(st, st.draft_answer) + _refuted(_check(loop, st, st.draft_answer))
    except Exception as exc:  # noqa: BLE001 — сверка не вправе ронять ответ
        loop._sensor_failed("draft_refutation", exc)
        return
    if not refuted:
        return
    listed = "; ".join(
        f"«{' '.join(c.text.split())[:200]}» — {c.reason.explanation} "
        f"(улика {c.reason.computed_from}: {str(c.reason.actual)[:120]})"
        for c in refuted[:_MAX_LISTED]
    )
    st.failure_history.append(ReplanTrigger(
        code="draft_contradicts_evidence", step_id="synthesis-refuted", tool_name=None,
        arguments={"codes": sorted({c.reason.code for c in refuted})},
        reason=(
            f"Your draft states {len(refuted)} claim(s) that your OWN evidence contradicts: "
            f"{listed}. Rewrite the answer from the same evidence. Say what the evidence "
            "actually shows — a probe with exit_code other than 0 confirms nothing except its "
            "error — or drop the claim. Do not repeat a contradicted claim in the first sentence."
        ),
        attempt=0,
    ))
    try:
        second = do_synthesize(SynthAttempt(index=1, adapt_context=False, is_final=True))
        after = (len(_outcome_contradictions(st, second)) + len(_refuted(_check(loop, st, second)))
                 if second else len(refuted))
    except Exception as exc:  # noqa: BLE001 — переписывание не вправе ронять ответ
        loop._sensor_failed("draft_refutation", exc)
        return
    loop.log.log("draft_contradicts_evidence", {
        "refuted_before": len(refuted), "refuted_after": after,
        "rewritten": bool(second) and after < len(refuted),
        "codes": sorted({c.reason.code for c in refuted}),
    })
    if second and after < len(refuted):
        st.draft_answer = second
