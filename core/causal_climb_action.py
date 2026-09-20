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
#: Живой прогон 2026-09-20: подъём отказал шесть раз подряд — «нужны
#: конкурирующие фальсифицируемые объяснения: выжило 0». Модель писала пары,
#: но перенос строки внутри объяснения (или пустая строка перед
#: предсказанием) ломал разбор, требовавший ДВУХ соседних строк. Номер тоже
#: стал необязательным: пара опознаётся по словам, а не по нумерации.
_PAIR_RE = re.compile(
    r"ОБЪЯСНЕНИЕ\s*\d*\s*:\s*(?P<statement>.+?)\s*\n\s*"
    r"ПРЕДСКАЗАНИЕ\s*\d*\s*:\s*(?P<predicts>.+?)"
    r"(?=\n\s*ОБЪЯСНЕНИЕ\s*\d*\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
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


def _decline(agent: Any, reason: str, *, llm_calls_spent: int = 0,
             result: str = "failed", **payload: Any) -> CampaignActionOutcome:
    """Отказ не носит продукта: artifact = работа (MIR-117), а причина —
    в журнале `causal_climb_declined`, молчаливых отказов нет. Потраченные
    до отказа вызовы едут в исходе — бюджет кампании не врёт (SPEC_WEAVE §3).

    `result` разделяет два разных отказа. «Не смог» — это `failed`. «Нечего
    делать» — это `idle`: живой прогон 2026-09-20, все наблюдения оказались
    закрыты, и шесть циклов подряд записали провал там, где была выполненная
    работа; драйв поломок считал их сломанными действиями."""
    _log(agent, "causal_climb_declined", {"reason": reason, **payload})
    return CampaignActionOutcome(result=result, llm_calls_spent=llm_calls_spent)


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
        return _decline(agent, "нет непокрытых наблюдений — подъёму нечего объяснять",
                        result="idle")
    record = naked[0]
    if dry_run:
        return _decline(
            agent, "dry_run: подъём не тратит и не пишет",
            fingerprint=record.fingerprint,
        )

    spent_before = _llm_calls(agent)
    try:
        # Кормление реальностью (проект агента): без списка модель выдумывала
        # имена журналов, и суд давился неведением — замер 2/2 кампаний.
        inventory = _live_inventory_block(workspace)
        raw = str(agent.llm.complete(
            system=_SYSTEM_PROMPT,
            user=(
                f"Сигналы дефекта: {', '.join(record.defect_signals)}\n"
                f"Наблюдение: {record.observed_mismatch[:1200]}\n"
                f"Улики: {', '.join(record.evidence_refs[:6])}\n"
                f"Повторений: {record.occurrences}"
                + (f"\n{inventory}" if inventory else "")
            ),
            max_tokens=600, temperature=0.4,
        ) or "")
    except Exception as exc:  # noqa: BLE001 — провод не роняет кампанию
        return _decline(agent, f"model_error:{type(exc).__name__}",
                        fingerprint=record.fingerprint)

    pairs = _parse_hypotheses(raw)
    # Ворота рождения (проект агента): проба в несуществующий файл убивает
    # пару здесь, а «меньше двух выживших» решает существующий порог ниже.
    pairs = _pairs_with_real_targets(pairs, workspace)
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
        subject=record.fingerprint,
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


AWAITING_EXPERIMENT_MARK = "awaiting_experiment"
#: Нота невыразимости (SPEC_WEAVE §2, авторство агента): развод гипотез не
#: выражается песочными целями — заявка ждёт слова человека; вечерний отчёт
#: находит все такие одним грепом по "spec_unexpressible".
SPEC_UNEXPRESSIBLE_NOTE = "spec_unexpressible_in_sandbox"


def _birth_experiment_spec(agent, claim):
    """Build a sandbox experiment spec that splits two competing live hypotheses, or None if impossible."""
    try:
        alive = [e for e in claim.explanations if e.alive]
        if len(alive) < 2:
            return None

        h1, h2 = alive[0], alive[1]
        targets = target_contracts()
        system = (
            "Ты разводишь ДВЕ конкурирующие гипотезы одним песочным экспериментом. "
            "Верни РОВНО ОДНУ строку в формате [exp: цель | A=рукав для гипотезы 1 | B=рукав для гипотезы 2 | след=наблюдаемое различие]. "
            "Цель - только из списка. Не можешь выразить развод доступными целями - верни ровно слово НЕВЫРАЗИМО."
        )
        user = (
            f"Гипотеза 1: {h1.predicts}\n"
            f"Гипотеза 2: {h2.predicts}\n"
            f"Доступные цели: {targets}"
        )
        response = agent.llm.complete(
            system=system, user=user, max_tokens=300, temperature=0.2
        )
        spec_text = response
        if "НЕВЫРАЗИМО" in spec_text:
            return None
        born = parse_experiment(spec_text)
        if born is None:
            return None
        why = spec_is_executable(born)
        if why:
            _log(agent, "causal_spec_rejected", {"why": why, "kind": "discriminate"})
            return None
    except Exception:  # noqa: BLE001 — рождение не роняет суд
        return None
    else:
        return spec_text


_INVARIANT_RE = re.compile(r"ИНВАРИАНТ\s*:\s*(?P<text>[^\n]+)", re.IGNORECASE)


def _birth_intervention_spec(agent, claim):
    """(spec_text, invariant) for a claim whose cause is chosen but unproven
    (block 4, M1): one hypothesis, arm A carries the cause, arm B removes it,
    and the model names the invariant the cause violates — the prose field no
    machine wrote before. None when inexpressible or the invariant is missing."""
    try:
        target = chosen_explanation(claim)
        if target is None:
            return None
        targets = target_contracts()
        system = (
            "Ты доказываешь ОДНУ уже выбранную причину её УСТРАНЕНИЕМ. Верни РОВНО ДВЕ строки:\n"
            "[exp: цель | A=вход, где причина присутствует | B=тот же вход без причины | след=наблюдаемое следствие]\n"
            "ИНВАРИАНТ: <какое правило системы нарушает эта причина - одной фразой>\n"
            "Цель - только из списка. Не можешь выразить - верни ровно слово НЕВЫРАЗИМО."
        )
        user = (
            f"Причина: {target.statement}\n"
            f"Её предсказание: {target.predicts}\n"
            f"Доступные цели: {targets}"
        )
        response = str(agent.llm.complete(
            system=system, user=user, max_tokens=300, temperature=0.2,
        ) or "")
        if "НЕВЫРАЗИМО" in response:
            return None
        born = parse_experiment(response)
        if born is None:
            return None
        why = spec_is_executable(born)
        if why:
            _log(agent, "causal_spec_rejected", {"why": why, "kind": "intervene"})
            return None
        found = _INVARIANT_RE.search(response)
        invariant = found.group("text").strip() if found else ""
        if not invariant:
            return None
    except Exception:  # noqa: BLE001 — рождение не роняет суд
        return None
    else:
        return response, invariant


def _apply_born_spec(claim, extra, spec_text, workspace, *, invariant=""):
    """Applies a born specification to the hypothesis it is for — the chosen
    one when the claim awaits its proof, else the first alive — and saves."""
    import dataclasses

    alive = [e for e in claim.explanations if e.alive]
    if not alive:
        return
    hypothesis = chosen_explanation(claim) if needs_intervention(claim) else alive[0]
    if hypothesis is None:
        return
    born = _EXP_RE.search(spec_text)
    piece = born.group(0) if born else spec_text
    new_predicts = hypothesis.predicts + " " + piece
    replaced = dataclasses.replace(hypothesis, predicts=new_predicts)
    new_explanations = tuple(
        replaced if e is hypothesis else e for e in claim.explanations
    )
    new_notes = tuple(n for n in claim.notes if n != AWAITING_EXPERIMENT_MARK)
    new_claim = dataclasses.replace(
        claim, explanations=new_explanations, notes=new_notes,
        violated_invariant=invariant or claim.violated_invariant,
    )
    save_claim(
        new_claim,
        workspace=workspace,
        directive=extra["directive"],
        machine_action=extra["machine_action"],
    )


def awaiting_experiment(claim) -> bool:
    """True, если заявка помечена "ждёт эксперимента" (метка присутствует в claim.notes).
    Чистая функция, только чтение."""
    return AWAITING_EXPERIMENT_MARK in claim.notes


def needs_intervention(claim) -> bool:
    """EXPLAINED без хода (блок 4, M1, 2026-09-03): объяснение выбрано пробами
    по журналам, а причина вмешательством не доказана. Замер: 15 живых заявок
    стояли так навсегда — `chosen` запирал слайс 3, ступени выше не было."""
    return bool(claim.chosen.strip()) and not claim.refuted_reason.strip() \
        and claim.intervention is None


def chosen_explanation(claim):
    """Живое объяснение, которое заявка выбрала, или None."""
    for exp in claim.explanations:
        if exp.alive and exp.statement == claim.chosen:
            return exp
    return None


def birth_candidates(workspace: str | Path):
    """Заявки, которым нужна спецификация: помеченные «ждёт эксперимента» и
    выбранные-без-вмешательства, у которых выбранное объяснение спеки не несёт.
    Нота невыразимости снимает заявку с рождения."""
    out = []
    for claim, extra in load_claims(workspace):
        if SPEC_UNEXPRESSIBLE_NOTE in claim.notes:
            continue
        if awaiting_experiment(claim):
            out.append((claim, extra))
            continue
        if needs_intervention(claim):
            target = chosen_explanation(claim)
            if target is not None and parse_experiment(target.predicts) is None:
                out.append((claim, extra))
    return tuple(out)


def discriminable_claims(workspace: str | Path):
    """Заявки без выбранного и без опровержения, где есть хоть одна живая проба."""
    out = []
    for claim, extra in load_claims(workspace):
        if claim.chosen.strip() or claim.refuted_reason.strip():
            continue
        if awaiting_experiment(claim):
            continue  # помеченные "ждёт эксперимента" пропускаются судом на входе
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


# ── Слайс 3: причина доказывается её устранением ─────────────────────────────
#
# Двурукавный эксперимент исполнения (DoVer: атрибуция из журналов —
# непроверенная гипотеза, пока не подтверждена исполнением):
#   [exp: цель | A=<вход с причиной> | B=<вход без причины> | след=<подстрока>]
# Следствие есть в A и исчезает в B → Intervention.proves_cause. Следствие в
# ОБОИХ рукавах → гипотеза опровергнута (её механизм различия не даёт).
# Нигде → эксперимент не воспроизвёл явление: неведение, не вердикт.
# Цели — ТОЛЬКО белый список чистых функций: ни файлов, ни сети, ни состояния.

_EXP_RE = re.compile(
    r"\[exp:\s*(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s*\|\s*"
    r"A=(?P<arm_a>[^|\]]+?)\s*\|\s*B=(?P<arm_b>[^|\]]+?)\s*\|\s*"
    r"след=(?P<effect>[^\]]+?)\s*\]",
    re.IGNORECASE,
)


def _run_reasoning_action_check(arm: str) -> str:
    """Адаптер белого списка: «текст ;; tool1,tool2» → строка отчёта сенсора."""
    from core.reasoning_action_check import check_reasoning_actions

    text, _, tools_raw = arm.partition(";;")
    tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
    report = check_reasoning_actions(text.strip(), tools)
    return (f"unjustified={sorted(report.unjustified_actions)!r} "
            f"mentioned_extra={sorted(report.mentioned_but_not_planned)!r}")


def _run_goal_first(arm: str) -> str:
    """Адаптер: «severity=… ;; action=… ;; goal_action=… ;; attempted=…» → выбор.

    `_goal_first` чиста: строит новые объекты, ничего не читает и не пишет.
    Взята целью 2026-09-20 по живому случаю: агент нашёл про неё все факты
    верно и сложил вывод наизнанку — «пайплайн не срабатывает из-за раннего
    возврата при critical/high», хотя по его же факту у пайплайна severity
    равен medium. Направление причины лексикой не установить; оно
    устанавливается прогоном обоих рукавов.
    """
    from core.best_next_action import BestNextAction
    from core.campaign import _goal_first

    fields: dict[str, str] = {}
    for part in arm.split(";;"):
        key, _, value = part.partition("=")
        if key.strip():
            fields[key.strip().lower()] = value.strip()
    incoming = BestNextAction(
        action=fields.get("action") or "observe",
        title="experiment arm",
        severity=fields.get("severity") or "medium",  # type: ignore[arg-type]
        priority=1,
        reason="experiment arm",
    )
    attempted = {t.strip() for t in (fields.get("attempted") or "").split(",") if t.strip()}
    chosen = _goal_first(incoming, attempted, fields.get("goal_action") or "")
    return (f"action={chosen.action} severity={chosen.severity} "
            f"decided_by={chosen.decided_by}")


#: Белый список целей эксперимента. Только ЧИСТЫЕ функции; расширение —
#: новая строка здесь плюс свидетель, никогда динамический импорт по имени.
_EXPERIMENT_TARGETS: dict[str, Any] = {
    "reasoning_action_check": _run_reasoning_action_check,
    "goal_first": _run_goal_first,
}

#: Договор цели: что цель берёт на вход и что печатает. Без него модель
#: писала рукава прозой («трейс с partially_achieved»), цель принимала её
#: молча и оба рукава давали один и тот же пустой отчёт — 46 прогонов
#: «неопределённо» на одной заявке (замер 2026-09-20).
_TARGET_CONTRACTS: dict[str, str] = {
    "reasoning_action_check": (
        "вход: «<текст рассуждения> ;; <инструмент1,инструмент2>»; "
        "печатает ровно: \"unjustified=['имя',…] mentioned_extra=['имя',…]\""
    ),
    "goal_first": (
        "решение режима «цель первой» по входящему действию; вход: "
        "«severity=<none|low|medium|high|critical> ;; action=<имя> ;; "
        "goal_action=<имя или пусто> ;; attempted=<имена через запятую>»; "
        "печатает ровно: \"action=<имя> severity=<…> decided_by=<…>\""
    ),
}


def target_contracts() -> str:
    """Список целей С ИХ ДОГОВОРАМИ: рукав — вход цели, след — подстрока вывода."""
    return "; ".join(
        f"{name} ({_TARGET_CONTRACTS.get(name, 'договор не описан')})"
        for name in _EXPERIMENT_TARGETS
    )


@dataclass(frozen=True)
class Experiment:
    """Машинно-исполняемая спецификация двурукавного вмешательства."""

    target: str
    arm_a: str
    arm_b: str
    effect: str


def parse_experiment(predicts: str) -> Experiment | None:
    """Спецификация из предсказания; цель вне белого списка — невалидна."""
    m = _EXP_RE.search(predicts or "")
    if not m:
        return None
    target = m.group("target").strip()
    if target not in _EXPERIMENT_TARGETS:
        return None
    arm_a = m.group("arm_a").strip()
    arm_b = m.group("arm_b").strip()
    effect = m.group("effect").strip()
    if not arm_a or not arm_b or not effect:
        return None
    return Experiment(target=target, arm_a=arm_a, arm_b=arm_b, effect=effect)


def spec_is_executable(spec: Experiment) -> str:
    """Пустая строка — спека исполнима; иначе причина, по которой она не опыт.

    Замер 2026-09-20: 46 исходов подряд «следствие не воспроизвелось ни в
    одном рукаве» на ОДНОЙ заявке. Разбор формы её пропускал — рукава были
    прозой («трейс с partially_achieved»), которую цель на вход не берёт, а
    `след=` было рассуждением, которого в выводе цели не бывает. Форма — не
    исполнимость; исполнимость проверяется исполнением.
    """
    runner = _EXPERIMENT_TARGETS.get(spec.target)
    if runner is None:
        return f"цели {spec.target!r} нет в белом списке"
    try:
        out_a = str(runner(spec.arm_a))
        out_b = str(runner(spec.arm_b))
    except Exception as err:  # noqa: BLE001 — сломанный рукав — не вердикт
        return f"рукав не исполняется: {type(err).__name__}"
    if spec.effect not in out_a and spec.effect not in out_b:
        return ("следствие не встречается ни в одном рукаве: "
                f"след={spec.effect[:60]!r} не подстрока вывода цели")
    if out_a == out_b:
        return "рукава дают один и тот же вывод — различать нечего"
    return ""


def retire_spec(predicts: str) -> str:
    """Предсказание без спецификации: мёртвая спека снимается, гипотеза живёт."""
    return _EXP_RE.sub("", predicts or "").strip()


def experimentable_claims(workspace: str | Path):
    """Открытые заявки, где живая гипотеза несёт спецификацию.

    Блок 4 (M1): заявка с выбранным ПО ПРОБАМ объяснением, но без
    вмешательства, на этой ступени открыта — журналы указали, доказывает
    эксперимент. Заявка с вмешательством или опровержением закрыта.
    """
    out = []
    for claim, extra in load_claims(workspace):
        if claim.refuted_reason.strip():
            continue
        if claim.chosen.strip():
            target = chosen_explanation(claim) if needs_intervention(claim) else None
            if target is not None and parse_experiment(target.predicts) is not None:
                out.append((claim, extra))
            continue
        if any(e.alive and parse_experiment(e.predicts) is not None
               for e in claim.explanations):
            out.append((claim, extra))
    return tuple(out)


def _retire_dead_specs(claim, extra, explanations, workspace, agent, retired):
    """Снять неисполнимые спеки и вернуть заявку под новое рождение.

    Неопределённый исход — не вердикт о гипотезе, а приговор СПЕКЕ: она не
    воспроизвела явление ни в одном рукаве. До 2026-09-20 он не стоил ей
    ничего, и следующий цикл брал ту же спеку первой — 46 прогонов подряд.
    """
    import dataclasses

    new_claim = dataclasses.replace(
        claim, explanations=tuple(explanations),
        notes=tuple(dict.fromkeys((*claim.notes, AWAITING_EXPERIMENT_MARK))),
    )
    key = save_claim(new_claim, workspace=workspace,
                     directive=extra.get("directive", ""),
                     machine_action="retire_dead_spec")
    _log(agent, "causal_spec_retired", {"claim_key": key, "retired": retired})
    return key


def run_claim_experiment(
    *, agent: Any, workspace: str | Path,
) -> CampaignActionOutcome:
    """Каждая живая спецификация исполняется; исход решает, не автор."""
    import dataclasses

    from core.causal_lesson import Intervention

    ws = Path(workspace)
    pending = experimentable_claims(ws)
    if not pending:
        return _decline(agent, "нет заявок со спецификациями эксперимента")
    claim, extra = pending[0]

    verdicts = 0
    retired: list[str] = []
    chosen = claim.chosen
    intervention = claim.intervention
    new_explanations = []
    for exp in claim.explanations:
        spec = parse_experiment(exp.predicts) if exp.alive else None
        if spec is None:
            new_explanations.append(exp)
            continue
        runner = _EXPERIMENT_TARGETS[spec.target]
        try:
            out_a = str(runner(spec.arm_a))
            out_b = str(runner(spec.arm_b))
        except Exception as err:  # noqa: BLE001 — сломанный рукав — не вердикт
            _log(agent, "causal_experiment_inconclusive", {
                "claim_key": extra["key"], "target": spec.target,
                "reason": f"рукав упал: {type(err).__name__}",
            })
            new_explanations.append(dataclasses.replace(
                exp, predicts=retire_spec(exp.predicts)))
            retired.append(f"рукав упал: {type(err).__name__}")
            continue
        in_a = spec.effect in out_a
        in_b = spec.effect in out_b
        if in_a and not in_b:
            verdicts += 1
            # Блок 4 (M1): выбор достаётся ПЕРВОМУ доказанному. Выбор по
            # пробам (журналы) — ещё не доказательство, и заявка без
            # вмешательства здесь открыта.
            if intervention is None:
                chosen = exp.statement
                intervention = Intervention(
                    mutated=(f"рукав B цели {spec.target}: "
                             f"предполагаемая причина устранена"),
                    predicted=f"следствие '{spec.effect}' исчезает",
                    observed=(f"A: следствие есть; B: следствия нет "
                              f"(A='{out_a[:120]}', B='{out_b[:120]}')"),
                    restored=True,  # чистая функция: состояние не менялось
                )
            new_explanations.append(exp)
        elif in_a and in_b:
            verdicts += 1
            if exp.statement == chosen and intervention is None:
                chosen = ""  # опровергнутое объяснение выбранным не остаётся
            new_explanations.append(dataclasses.replace(
                exp,
                refuted_by=(f"эксперимент {spec.target}: следствие "
                            f"'{spec.effect}' в ОБОИХ рукавах — механизм "
                            f"гипотезы различия не даёт"),
            ))
        else:
            _log(agent, "causal_experiment_inconclusive", {
                "claim_key": extra["key"], "target": spec.target,
                "reason": "следствие не воспроизвелось ни в одном рукаве",
            })
            new_explanations.append(dataclasses.replace(
                exp, predicts=retire_spec(exp.predicts)))
            retired.append("следствие не воспроизвелось ни в одном рукаве")

    if verdicts == 0:
        if retired:
            key = _retire_dead_specs(claim, extra, new_explanations,
                                     ws, agent, tuple(retired))
            return _decline(agent, "спецификация ничего не воспроизвела — снята, "
                            "заявка ждёт новой", claim_key=key, result="idle")
        return _decline(agent, "эксперименты не дали ни одного вердикта",
                        claim_key=extra["key"])

    updated = dataclasses.replace(
        claim, explanations=tuple(new_explanations),
        chosen=chosen, intervention=intervention,
    )
    key = save_claim(updated, workspace=ws,
                     machine_action="run_claim_experiment")
    _log(agent, "causal_climb_experimented", {
        "claim_key": key, "verdicts": verdicts,
        "chosen": bool(chosen.strip()),
        "proves_cause": bool(intervention and intervention.proves_cause),
    })
    return CampaignActionOutcome(
        result="completed",
        subject=key,
        artifact=(
            f"заявка {key}: экспериментальных вердиктов {verdicts}"
            + (f", причина доказана вмешательством: '{chosen[:60]}'"
               if chosen.strip() and intervention else "")
        ),
        work_done=True,
    )


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
        # Суд сам помечает исчерпанную заявку "ждёт эксперимента" (решение
        # агента, awaiting_experiment.py): пробы не развели соперников — дальше
        # только вмешательство; фильтр выше её больше не возьмёт. Рождение
        # спецификации ЖИВЁТ НЕ ЗДЕСЬ: суд без модели на любом пути — его
        # конституция (INVARIANT_CLASH, вердикт агента: путь (б)).
        marked = dataclasses.replace(
            claim, notes=(*claim.notes, AWAITING_EXPERIMENT_MARK))
        save_claim(marked, workspace=ws, directive=extra["directive"],
                   machine_action=extra["machine_action"])
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
        subject=key,
        artifact=(
            f"заявка {key}: вердиктов {verdicts}, живых {len(alive)}"
            + (f", выбрано '{chosen[:60]}'" if chosen.strip() else "")
            + (", REFUTED целиком" if refuted_reason.strip() else "")
        ),
        work_done=True,
    )


# --- Ворота рождения проб и кормление реальностью: проект и обе функции —
# авторства агента (GUARD_DESIGN + два груза 2026-08-30, оба с первого захода);
# курьер перенёс дословно. Замер 2/2: объяснитель выдумывал имена журналов,
# суд честно давился неведением, кампании гасли датчиком петли.
def _pairs_with_real_targets(pairs, workspace):
    """Пробу судят при рождении, чтобы суд не давился неведением."""
    survivors = []
    for statement, predicts in pairs:
        probe = parse_probe(predicts)
        if probe is None:
            survivors.append((statement, predicts))
            continue
        if (Path(workspace) / probe.rel_path).exists():
            survivors.append((statement, predicts))
    return survivors


def _live_inventory_block(workspace):
    """Модель пишет пробы по списку настоящих файлов, а не по памяти."""
    lines = []
    for dirname in _PROBE_DIRS:
        d = Path(workspace) / dirname
        if not d.is_dir():
            continue
        names = sorted(
            p.name for p in d.iterdir() if p.is_file()
        )[:40]
        if names:
            lines.append(f"{dirname}/: {', '.join(names)}")
    if not lines:
        return ""
    return "Существующие файлы для проб (только эти пути годятся в [probe: ...]):\n" + "\n".join(lines)


def birth_experiment_specs(
    *, agent: Any, workspace: str | Path,
) -> CampaignActionOutcome:
    """Помеченной заявке — песочный эксперимент, либо честная нота невыразимости.

    Отдельное действие кампании по конституционному вердикту агента
    (INVARIANT_CLASH, путь (б)): суд без модели на любом пути, рождение — вне
    суда. Сборка — объединение его принятых кусков (обход пар и фильтр из
    его поставки; замер трат — SPEC_WEAVE §3; событие рождения — §4; путь
    невыразимости — §2 и принятая вплетка суда); оба двигателя не удержали
    композицию целиком — граница забанкована в леджере, третий замер.
    """
    import dataclasses

    ws = Path(workspace)
    candidates = birth_candidates(ws)
    if not candidates:
        return _decline(agent, "нет помеченных заявок без спецификаций")

    claim, extra = candidates[0]
    spent_before = _llm_calls(agent)
    invariant = ""
    if needs_intervention(claim):
        # Блок 4 (M1): выбранной по пробам причине — эксперимент на устранение
        # и имя нарушенного инварианта; без них EXPLAINED не имел выхода.
        pair = _birth_intervention_spec(agent, claim)
        spec_text = pair[0] if pair else None
        invariant = pair[1] if pair else ""
    else:
        spec_text = _birth_experiment_spec(agent, claim)
    birth_calls = max(0, _llm_calls(agent) - spent_before)

    if spec_text is not None:
        _apply_born_spec(claim, extra, spec_text, ws, invariant=invariant)
        born = parse_experiment(spec_text)
        target = born.target if born else ""
        _log(agent, "spec_born", {"claim_key": extra["key"], "target": target,
                                  "invariant_named": bool(invariant)})
        return CampaignActionOutcome(
            result="completed",
            llm_calls_spent=birth_calls,
            subject=extra["key"],
            artifact=(
                f"заявка {extra['key']} получила песочный эксперимент "
                f"цели '{target}'"
            ),
            work_done=True,
        )

    marked = dataclasses.replace(
        claim, notes=(*claim.notes, SPEC_UNEXPRESSIBLE_NOTE))
    save_claim(marked, workspace=ws, directive=extra["directive"],
               machine_action=extra["machine_action"])
    _log(agent, "spec_unexpressible", {"claim_key": extra["key"]})
    return _decline(
        agent, "спецификация невыразима песочными целями",
        llm_calls_spent=birth_calls, claim_key=extra["key"],
    )
