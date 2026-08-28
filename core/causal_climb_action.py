"""Слайс 1 органа подъёма: наблюдение → конкурирующие объяснения (MIR-096).

Проект, конституция и литературные основания:
docs/audit/CAUSAL_CLIMB_ORGAN_DESIGN.md. Коротко: гипотеза без предсказания
не принимается; меньше двух выживших — отказ без записи; автор не судит себя
(Huang ICLR'24) — заявка сохраняется с chosen == "" до различения; dry-run не
тратит и не пишет. Различение и чеканка урока — слайсы 2–3, не здесь.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.campaign_types import CampaignActionOutcome
from core.causal_claim_store import load_claims, save_claim
from core.causal_climb import propose_explanation
from core.causal_lesson import CausalClaim, Observation
from core.causal_store import CausalObservationStore, ObservationRecord

_OBSERVATIONS_RELPATH = Path("data") / "causal_observations.jsonl"

#: Пары «ОБЪЯСНЕНИЕ N / ПРЕДСКАЗАНИЕ N» из ответа модели. Разбор построчный
#: и терпимый к хвостам: незнакомые строки игнорируются, пустые поля убивают
#: пару на воротах качества, не в разборе.
_PAIR_RE = re.compile(
    r"ОБЪЯСНЕНИЕ\s*\d+\s*:\s*(?P<statement>[^\n]*)\n"
    r"\s*ПРЕДСКАЗАНИЕ\s*\d+\s*:\s*(?P<predicts>[^\n]*)",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "Ты расследуешь ОДНО наблюдение о сбое собственной системы. Выдвини "
    "ровно 2-3 КОНКУРИРУЮЩИХ объяснения причины. Формат, строго построчно:\n"
    "ОБЪЯСНЕНИЕ 1: <одна проверяемая причина, назови механизм>\n"
    "ПРЕДСКАЗАНИЕ 1: <что наблюдалось бы в журналах/переигровке, будь оно "
    "верным, и чего НЕ наблюдалось бы иначе>\n"
    "ОБЪЯСНЕНИЕ 2: ...\nПРЕДСКАЗАНИЕ 2: ...\n"
    "Объяснения обязаны исключать друг друга хотя бы одним предсказанием. "
    "Где возможно, заверши предсказание машинной пробой в форме "
    "[probe: logs/<файл> | <подстрока> | есть] или [probe: data/<файл> | "
    "<подстрока> | нет] — тогда журналы рассудят сами; без пробы гипотезу "
    "рассудит только вмешательство. "
    "НЕ выбирай победителя: выбор сделает проверка, не автор."
)


def unexplained_observations(workspace: str | Path) -> tuple[ObservationRecord, ...]:
    """Наблюдения, чей отпечаток не покрыт ни одной заявкой.

    Самое повторяющееся — первым: повторяемость = сильнейший повод
    расследования (класс MIR-100); при равенстве — свежее.
    """
    store = CausalObservationStore(Path(workspace) / _OBSERVATIONS_RELPATH)
    records = store.load()
    if not records:
        return ()
    from core.causal_store import observation_fingerprint

    covered = {
        observation_fingerprint(claim.observation)
        for claim, _extra in load_claims(workspace)
    }
    naked = [r for r in records if r.fingerprint not in covered]
    naked.sort(key=lambda r: (-r.occurrences, r.last_seen), reverse=False)
    return tuple(naked)


def _parse_hypotheses(raw: str) -> list[tuple[str, str]]:
    """(объяснение, предсказание) — только пары, где живы ОБА поля."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _PAIR_RE.finditer(raw or ""):
        statement = m.group("statement").strip()
        predicts = m.group("predicts").strip()
        if not statement or not predicts or statement in seen:
            continue
        seen.add(statement)
        out.append((statement, predicts))
    return out


def _decline(agent: Any, reason: str, **payload: Any) -> CampaignActionOutcome:
    """Отказ не носит продукта: artifact = работа (MIR-117), а причина —
    в журнале `causal_climb_declined`, молчаливых отказов нет."""
    _log(agent, "causal_climb_declined", {"reason": reason, **payload})
    return CampaignActionOutcome(result="failed")


def _log(agent: Any, event: str, payload: dict) -> None:
    log = getattr(agent, "log", None)
    if log is not None:
        try:
            log.log(event, payload)
        except Exception:  # noqa: BLE001, S110 — журнал не роняет подъём
            pass


def explain_causal_observation(
    *, agent: Any, workspace: str | Path, dry_run: bool = False,
) -> CampaignActionOutcome:
    """Одно наблюдение → заявка с ≥2 фальсифицируемыми объяснениями."""
    naked = unexplained_observations(workspace)
    if not naked:
        return _decline(agent, "нет непокрытых наблюдений — подъёму нечего объяснять")
    record = naked[0]
    if dry_run:
        return _decline(
            agent, "dry_run: подъём не тратит и не пишет",
            fingerprint=record.fingerprint,
        )

    spent_before = _llm_calls(agent)
    try:
        raw = str(agent.llm.complete(
            system=_SYSTEM_PROMPT,
            user=(
                f"Сигналы дефекта: {', '.join(record.defect_signals)}\n"
                f"Наблюдение: {record.observed_mismatch[:1200]}\n"
                f"Улики: {', '.join(record.evidence_refs[:6])}\n"
                f"Повторений: {record.occurrences}"
            ),
            max_tokens=600, temperature=0.4,
        ) or "")
    except Exception as exc:  # noqa: BLE001 — провод не роняет кампанию
        return _decline(agent, f"model_error:{type(exc).__name__}",
                        fingerprint=record.fingerprint)

    pairs = _parse_hypotheses(raw)
    if len(pairs) < 2:
        # Ворота качества: гипотеза без предсказания мертва, а одна выжившая —
        # подтверждение задним числом, не расследование.
        return _decline(
            agent,
            f"нужны конкурирующие фальсифицируемые объяснения: выжило {len(pairs)}",
            fingerprint=record.fingerprint,
            answer_head=" ".join((raw or "").split())[:200],
        )

    claim = CausalClaim(
        observation=Observation(
            episode_id=(record.episode_ids[0] if record.episode_ids
                        else record.fingerprint),
            trace_id="",
            run_id="campaign_climb",
            defect_signals=record.defect_signals,
            evidence_refs=record.evidence_refs,
            observed_mismatch=record.observed_mismatch,
        ),
        explanations=tuple(
            propose_explanation(statement, author="agent", predicts=predicts)
            for statement, predicts in pairs[:3]
        ),
        # chosen НАМЕРЕННО пуст: выбор — исход различения (слайс 2), не автора.
    )
    key = save_claim(claim, workspace=workspace,
                     machine_action="explain_causal_observation")
    _log(agent, "causal_climb_explained", {
        "claim_key": key, "fingerprint": record.fingerprint,
        "hypotheses": len(claim.explanations),
    })
    return CampaignActionOutcome(
        result="completed",
        llm_calls_spent=max(0, _llm_calls(agent) - spent_before),
        artifact=(
            f"заявка {key}: {len(claim.explanations)} конкурирующих объяснения "
            f"с предсказаниями для '{record.fingerprint}'; выбор — за различением"
        ),
        work_done=True,
    )


def _llm_calls(agent: Any) -> int:
    llm = getattr(agent, "llm", None)
    try:
        return int(getattr(llm, "call_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


# ── Слайс 2: детерминированное различение ────────────────────────────────────
#
# Проба — машинная часть предсказания: `[probe: путь | подстрока | есть|нет]`.
# Пути только ВНУТРИ logs/ и data/ рабочей области: побег («..», абсолютный,
# чужой каталог) делает пробу невалидной, и она НЕ читается. Различение не
# зовёт модель вовсе: слово автора — не улика (Huang ICLR'24), улика — файл.

_PROBE_RE = re.compile(
    r"\[probe:\s*(?P<path>[^|\]]+?)\s*\|\s*(?P<needle>[^|\]]+?)\s*\|\s*"
    r"(?P<expect>есть|нет)\s*\]",
    re.IGNORECASE,
)

_PROBE_DIRS = ("logs", "data")


@dataclass(frozen=True)
class Probe:
    """Машинно-проверяемая половина предсказания."""

    rel_path: str
    needle: str
    expect_present: bool


def parse_probe(predicts: str) -> Probe | None:
    """Проба из текста предсказания; невалидная (побег, чужой каталог) — None."""
    m = _PROBE_RE.search(predicts or "")
    if not m:
        return None
    rel = m.group("path").strip().replace("\\", "/")
    if not rel or rel.startswith(("/", "~")) or ":" in rel or ".." in rel:
        return None
    if rel.split("/", 1)[0] not in _PROBE_DIRS:
        return None
    needle = m.group("needle").strip()
    if not needle:
        return None
    return Probe(
        rel_path=rel,
        needle=needle,
        expect_present=m.group("expect").strip().lower() == "есть",
    )


def discriminable_claims(workspace: str | Path):
    """Заявки без выбранного и без опровержения, где есть хоть одна живая проба."""
    out = []
    for claim, extra in load_claims(workspace):
        if claim.chosen.strip() or claim.refuted_reason.strip():
            continue
        probed = any(
            e.alive and parse_probe(e.predicts) is not None
            for e in claim.explanations
        )
        if probed:
            out.append((claim, extra))
    return tuple(out)


def _run_probe(workspace: Path, probe: Probe) -> bool | None:
    """True = подстрока найдена, False = нет, None = цель нечитаема (не вердикт)."""
    base = (workspace / probe.rel_path.split("/", 1)[0]).resolve()
    target = (workspace / probe.rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    candidates = (sorted(target.parent.glob(target.name))
                  if any(ch in target.name for ch in "*?") else [target])
    found_any_file = False
    for path in candidates:
        if not path.is_file():
            continue
        found_any_file = True
        try:
            if probe.needle in path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False if found_any_file else None


def discriminate_causal_claim(
    *, agent: Any, workspace: str | Path,
) -> CampaignActionOutcome:
    """Одна заявка: каждая живая проба встречает журнал; исход решает, не автор."""
    import dataclasses

    ws = Path(workspace)
    pending = discriminable_claims(ws)
    if not pending:
        return _decline(agent, "нет заявок, различимых пробами")
    claim, extra = pending[0]

    verdicts = 0
    confirmed: set[str] = set()
    new_explanations = []
    for exp in claim.explanations:
        probe = parse_probe(exp.predicts) if exp.alive else None
        if probe is None:
            new_explanations.append(exp)
            continue
        seen = _run_probe(ws, probe)
        if seen is None:
            _log(agent, "causal_probe_inconclusive", {
                "claim_key": extra["key"], "path": probe.rel_path,
                "reason": "цель пробы нечитаема — неведение, не вердикт",
            })
            new_explanations.append(exp)
            continue
        verdicts += 1
        if seen != probe.expect_present:
            new_explanations.append(dataclasses.replace(
                exp,
                refuted_by=(
                    f"probe {probe.rel_path}: ожидалось "
                    f"{'есть' if probe.expect_present else 'нет'} "
                    f"'{probe.needle}', наблюдалось обратное"
                ),
            ))
        else:
            confirmed.add(exp.statement)
            new_explanations.append(exp)

    alive = [e for e in new_explanations if e.alive]
    chosen = claim.chosen
    refuted_reason = claim.refuted_reason
    if not alive:
        # Опровержение всех — тоже знание, заявка закрывается честно.
        refuted_reason = "все объяснения опровергнуты пробами по журналам"
    elif (len(alive) == 1 and len(new_explanations) > 1
          and alive[0].statement in confirmed):
        # Выбор = исход различения, и только ИСПЫТАННОМУ: выживание без
        # пробы — не победа, такой соперник ждёт вмешательства (слайс 3).
        chosen = alive[0].statement

    advanced = (verdicts > 0
                and (chosen != claim.chosen
                     or refuted_reason != claim.refuted_reason
                     or new_explanations != list(claim.explanations)))
    if not advanced:
        return _decline(agent, "ни одного вердикта: пробы не решили ничего",
                        claim_key=extra["key"])

    updated = dataclasses.replace(
        claim, explanations=tuple(new_explanations),
        chosen=chosen, refuted_reason=refuted_reason,
    )
    key = save_claim(updated, workspace=ws,
                     machine_action="discriminate_causal_claim")
    _log(agent, "causal_climb_discriminated", {
        "claim_key": key, "verdicts": verdicts,
        "alive": len(alive), "chosen": bool(chosen.strip()),
        "refuted": bool(refuted_reason.strip()),
    })
    return CampaignActionOutcome(
        result="completed",
        artifact=(
            f"заявка {key}: вердиктов {verdicts}, живых {len(alive)}"
            + (f", выбрано '{chosen[:60]}'" if chosen.strip() else "")
            + (", REFUTED целиком" if refuted_reason.strip() else "")
        ),
        work_done=True,
    )
