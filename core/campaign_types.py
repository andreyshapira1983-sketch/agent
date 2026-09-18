"""Campaign value types: configuration, per-action outcome, and the result of a finished campaign.

Extracted from `core/campaign` by autonomous self-build module split.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#: `kw_only` не украшение. Ревизия PR #333: `success_check` встал ВТОРЫМ
#: полем, и любой позиционный вызывающий с этого дня молча получал
#: критерий успеха там, где передавал что-то другое. Порядок полей у
#: растущей настройки — не договор, и притворяться договором он не
#: должен: пусть ошибка будет громкой, а не тихой.
@dataclass(frozen=True, kw_only=True)
class CampaignConfig:
    goal: str = "project health"
    #: Критерий успеха цели — дословно тот, с которым её выбрала хартия.
    #: Пустая строка значит «критерий не назван», и это честное состояние:
    #: четыре точки входа задают цель строкой без всякой проверки.
    success_check: str = ""
    max_cycles: int = 24
    max_llm_calls: int = 100
    max_cost_units: int = 0
    max_idle_streak: int = 3
    dry_run: bool = True
    report_every: int = 1
    idle_recheck_seconds: int = 600
    max_wall_clock_seconds: int = 0
    cycle_pause_seconds: int = 0
    max_consecutive_errors: int = 3
    max_unproductive_streak: int = 0
    #: Межзапусковый потолок трат на ОДНУ сигнатуру действия (MIR-149).
    #: 400 = два дневных прогона гранта; число одобрено оператором 2026-08-27.
    #: Превышение — не молчаливое исполнение, а вопрос ему (result="cost_cap").
    #: 400 -> 1200 (2026-09-01, слово оператора «подними потолок»): счётчик
    #: копится за ВСЕ запуски и не имеет окна, поэтому 411 единиц, потраченных
    #: за неделю на `propose_engineering_task`, сделали действие мёртвым
    #: навсегда — четыре самостоятельных запуска подряд не дали работы.
    #: Поднято втрое, а не сброшено: история трат остаётся видимой, и стена
    #: вернётся, когда действие снова начнёт жечь бюджет впустую. Настоящее
    #: лекарство — окно (траты за N суток) — НЕ вводится: это смена политики,
    #: а не её исполнение, и требует отдельного слова оператора.
    #: 1200 -> 0 (2026-09-01, слово оператора «счётчик вообще должны снять»):
    #: межзапусковый потолок ВЫКЛЮЧЕН на время длинного прогона. Механизм цел
    #: и включается одним числом; выключен он потому, что за сутки не поймал
    #: ни одной опасной траты, зато четырежды остановил работу до её начала.
    #: Дневной и прогонный бюджеты (max_llm_calls, max_cost_units) остаются.
    max_cost_units_per_signature: int = 0

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.max_idle_streak < 1:
            raise ValueError("max_idle_streak must be >= 1")
        if self.max_llm_calls < 0:
            raise ValueError("max_llm_calls must be >= 0 (0 = unlimited)")
        if self.max_cost_units < 0:
            raise ValueError("max_cost_units must be >= 0 (0 = unlimited)")
        if self.report_every < 1:
            raise ValueError("report_every must be >= 1")
        if self.idle_recheck_seconds < 0:
            raise ValueError("idle_recheck_seconds must be >= 0")
        if self.max_wall_clock_seconds < 0:
            raise ValueError("max_wall_clock_seconds must be >= 0 (0 = unlimited)")
        if self.cycle_pause_seconds < 0:
            raise ValueError("cycle_pause_seconds must be >= 0 (0 = no pause)")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")
        if self.max_unproductive_streak < 0:
            raise ValueError("max_unproductive_streak must be >= 0 (0 = off)")
        if self.max_cost_units_per_signature < 0:
            raise ValueError("max_cost_units_per_signature must be >= 0 (0 = off)")


@dataclass(frozen=True)
class CampaignActionOutcome:
    result: str
    llm_calls_spent: int = 0
    cost_units_spent: int = 0
    proposal: str | None = None
    artifact: str | None = None
    #: Слово производителя «работа сделана» — для исходов без продукта
    #: (MIR-117, норма A). Продукт говорит сам за себя через did_work.
    work_done: bool = False
    # предмет шага - отпечаток наблюдения у объяснителя, ключ заявки у суда;
    # пустая строка у действий без предмета (WEAVE ЗАЗОР 1, авторство агента)
    subject: str = ""
    #: Слово исполнителя «работа была, продукта нет» (замер 2026-09-20).
    #: Отказ ПОСЛЕ отработавшей работы неотличим от отказа ДО её начала, пока
    #: единственный свидетель попытки — потраченные деньги: эксперимент из
    #: двух рукавов исполняется бесплатно, и девять его безвердиктных проходов
    #: подряд кампания приняла за девять первых. Ставит это слово только тот,
    #: кто знает, что работа шла; умолчание сохраняет прежний смысл `ran`.
    attempted: bool = False
    #: Причина ИСХОДА словами исполнителя — не путать с `reason` записи цикла,
    #: где лежит повод ВЫБРАТЬ действие. До 2026-09-20 причина отказа жила
    #: только в журнале агента, и девять падений в реестре кампании выглядели
    #: беспричинными. Пустая строка = исполнитель причины не назвал.
    note: str = ""

    @property
    def did_work(self) -> bool:
        """Работа сделана: продукт существует или производитель сказал сам."""
        return self.work_done or self.proposal is not None or self.artifact is not None

    @property
    def ran(self) -> bool:
        """Попытка была: что-то потрачено, что-то сделано или работа шла.

        Отказ до старта (0 трат, 0 продукта, `attempted` не поднят) попыткой
        НЕ является — банить его подпись значило бы лгать «прежний проход не
        снял сигнал» (MIR-117). Но работа, которая ничего не стоила, — всё
        ещё работа: без `attempted` бесплатное действие повторялось вечно,
        а платное отсекалось со второго захода (замер 2026-09-20).
        """
        return (
            self.did_work
            or self.attempted
            or self.llm_calls_spent > 0
            or self.cost_units_spent > 0
        )


@dataclass
class CampaignResult:
    status: str
    goal: str
    stop_reason: str
    cycles_run: int
    records: list[Any] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    clarification: dict[str, Any] | None = None
    #: Вердикт по СОБСТВЕННОМУ критерию цели (core/campaign_verdict.py).
    #: `None` = не судили: так выглядит результат, собранный не кампанией.
    success_verdict: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal": self.goal,
            "stop_reason": self.stop_reason,
            "cycles_run": self.cycles_run,
            "totals": self.totals,
            "records": [r.to_dict() for r in self.records],
            "clarification": self.clarification,
            "success_verdict": self.success_verdict,
        }

    def user_summary(self) -> str:
        lines = [
            "=== autonomous campaign ===",
            f"status={self.status}  goal={self.goal!r}  stop_reason={self.stop_reason or '-'}",
            (
                f"cycles={self.cycles_run}  "
                f"useful={self.totals.get('useful_cycles', 0)}  "
                f"idle={self.totals.get('idle_cycles', 0)}  "
                f"repeats={self.totals.get('repeat_cycles', 0)}  "
                f"errors={self.totals.get('error_cycles', 0)}  "
                # Падение действия и вылетевшее исключение — разные события, и
                # сливать их нельзя. Но показывать только вылеты значит
                # отчитаться `errors=0` о прогоне, где девять циклов упали
                # (замер 2026-09-20): формально верно, человеку — ложь.
                f"failed={self.totals.get('failed_cycles', 0)}  "
                f"llm_calls={self.totals.get('llm_calls', 0)}  "
                f"cost_units={self.totals.get('cost_units', 0)}  "
                f"proposals={self.totals.get('proposals', 0)}  "
                f"artifacts={self.totals.get('artifacts', 0)}  "
                f"goal_drove={self.totals.get('goal_drove_cycles', 0)}  "
                # Цена полезного цикла — всегда видимое зеркало трат (MIR-177).
                # «-» при нуле полезных: неопределённость не ноль и не бесконечность.
                f"units_per_useful="
                f"{round(self.totals.get('cost_units', 0) / u, 1) if (u := self.totals.get('useful_cycles', 0)) else '-'}"
            ),
        ]
        # Вердикт по цели стоит ВЫШЕ циклов: `status=completed` у прогона,
        # который цели не достиг, формально верен («смена отработана»), а
        # человеку читается как успех.
        if self.success_verdict:
            from core.campaign_verdict import verdict_summary_line
            lines.append(verdict_summary_line(self.success_verdict))
        for record in self.records:
            lines.append(f"  {record.user_summary()}")
        if self.clarification and self.clarification.get("questions"):
            lines.append("--- нужно уточнение (режим вопроса) ---")
            lines.append("Я не могу безопасно продолжить. Уточни:")
            for question in self.clarification["questions"]:
                lines.append(f"  - {question}")
            forbidden = self.clarification.get("forbidden_actions") or []
            if forbidden:
                lines.append(f"  (пока запрещено: {', '.join(forbidden)})")
        return "\n".join(lines)
