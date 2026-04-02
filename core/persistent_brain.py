# PersistentBrain — Полная персистентная память агента
# Сохраняет и загружает ВСЁ состояние агента на диск.
# Ни один бит опыта не теряется при перезапуске.
# pylint: disable=protected-access

from __future__ import annotations

import os
import json
import time
import threading
import re


class PersistentBrain:
    """
    Полная персистентная память агента — мост между RAM и диском.

    Сохраняет:
        - knowledge: long_term, episodic, semantic (Слой 2)
        - goals: все цели, прогресс, иерархия (Слой 37)
        - episodes: буфер experience replay (Слой 36)
        - skills: выученные навыки (Слой 29)
        - identity: capabilities, proficiency, performance (Слой 45)
        - social: актёры, отношения, доверие (Слой 43)
        - reflections: инсайты, рефлексии (Слой 10)
        - strategies: стратегии + все предложения (Слой 12)
        - conversations: история разговоров
        - lessons: извлечённые уроки
        - thoughts: мысли ProactiveMind
        - evolution: лог эволюции агента
        - stats: общая статистика

    Автосохранение каждые 60 секунд в фоновом потоке.
    """

    AUTOSAVE_INTERVAL = 60

    # Дисковый бюджет агента — 100 ГБ
    DISK_BUDGET_GB = 100

    # Лимиты хранения — фактически неограниченные в рамках дискового бюджета
    MAX_CONVERSATIONS   = 1_000_000
    MAX_LESSONS         = 500_000
    MAX_EPISODES        = 100_000
    MAX_SOLVER_CASES    = 100_000
    MAX_SOLVER_JOURNAL  = 100_000
    MAX_EVOLUTION       = 50_000
    MAX_EPISODIC_MEM    = 100_000
    MAX_PATTERNS        = 20_000
    MAX_PERF_HISTORY    = 50_000
    MAX_ATTENTION       = 50_000
    MAX_TIMELINE        = 100_000
    MAX_CAUSAL_LINKS    = 100_000
    MAX_RELATIONS       = 50_000
    MAX_LEARNED         = 100_000
    MAX_SANDBOX_RUNS    = 50_000

    def __init__(
        self,
        data_dir: str,
        knowledge=None,
        experience_replay=None,
        self_improvement=None,
        social=None,
        goal_manager=None,
        skill_library=None,
        identity=None,
        reflection=None,
        proactive_mind=None,
        attention=None,
        temporal=None,
        causal=None,
        environment_model=None,
        learning=None,
        autonomous_loop=None,
        sandbox=None,
        capability_discovery=None,
        monitoring=None,
    ):
        self.data_dir = data_dir
        self.knowledge = knowledge
        self.experience = experience_replay
        self.self_improvement = self_improvement
        self.social = social
        self.goal_manager = goal_manager
        self.skill_library = skill_library
        self.identity = identity
        self.reflection = reflection
        self.proactive_mind = proactive_mind
        self.autonomous_loop = autonomous_loop
        self.attention = attention
        self.temporal = temporal
        self.causal = causal
        self.environment_model = environment_model
        self.learning = learning
        self.sandbox = sandbox
        self.capability_discovery = capability_discovery
        self.monitoring = monitoring

        # Архитектура агента — загружается один раз при старте, read-only.
        # LLM видит краткую выжимку, но изменить _architecture не может
        # (нет ни одного метода записи в эту переменную).
        self._architecture: dict = {}   # {"layers": [...], "summary": "..."}

        self._conversations: list[dict] = []
        self._lessons: list[dict] = []
        self._evolution_log: list[dict] = []
        self._solver_cases: list[dict] = []
        self._exact_solver_journal: list[dict] = []
        self._pending_learning_quality: dict = {}
        self._stats = {
            "total_cycles": 0,
            "useful_cycles": 0,
            "weak_cycles": 0,
            "total_conversations": 0,
            "successful_tasks": 0,
            "failed_tasks": 0,
            "total_lessons": 0,
            "first_boot": None,
            "last_boot": None,
            "boot_count": 0,
        }

        self._lock = threading.Lock()
        self._autosave_thread: threading.Thread | None = None
        self._running = False

        os.makedirs(data_dir, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════════
    # ЗАГРУЗКА ПРИ СТАРТЕ
    # ═══════════════════════════════════════════════════════════════════════

    def load(self):
        """Загружает ВСЁ состояние с диска в компоненты агента."""
        with self._lock:
            self._load_architecture()   # read-only; грузится первым, не зависит от диска агента
            self._load_stats()
            self._load_knowledge()
            self._load_conversations()
            self._load_lessons()
            self._load_solver_cases()
            self._load_exact_solver_journal()
            self._load_strategies()
            self._load_evolution()
            self._load_goals()
            self._load_episodes()
            self._load_skills()
            self._load_identity()
            self._load_social()
            self._load_reflections()
            self._load_thoughts()
            self._load_learning_quality()
            self._load_attention()
            self._load_temporal()
            self._load_causal()
            self._load_environment()
            self._load_learning()
            self._load_sandbox()
            self._load_capability_stats()

            now = time.time()
            if not self._stats["first_boot"]:
                self._stats["first_boot"] = now
            self._stats["last_boot"] = now
            self._stats["boot_count"] += 1

            self._log(
                f"Память загружена: "
                f"{self._count_knowledge()} знаний, "
                f"{len(self._conversations)} разговоров, "
                f"{len(self._lessons)} уроков, "
                f"загрузка #{self._stats['boot_count']}"
            )
            try:
                du = self.disk_usage()
                self._log(
                    f"Дисковый бюджет: {du['used_gb']} ГБ из {du['budget_gb']} ГБ "
                    f"({du['used_pct']}%), свободно {du['free_gb']} ГБ"
                )
            except (OSError, ValueError, KeyError, TypeError):
                pass

    # ═══════════════════════════════════════════════════════════════════════
    # СОХРАНЕНИЕ
    # ═══════════════════════════════════════════════════════════════════════

    def save(self):
        """Сохраняет ВСЁ состояние на диск."""
        with self._lock:
            self._save_stats()
            self._save_knowledge()
            self._save_conversations()
            self._save_lessons()
            self._save_solver_cases()
            self._save_exact_solver_journal()
            self._save_strategies()
            self._save_evolution()
            self._save_goals()
            self._save_episodes()
            self._save_skills()
            self._save_identity()
            self._save_social()
            self._save_reflections()
            self._save_thoughts()
            self._save_learning_quality()
            self._save_attention()
            self._save_temporal()
            self._save_causal()
            self._save_environment()
            self._save_learning()
            self._save_sandbox()
            self._save_capability_stats()

    # ═══════════════════════════════════════════════════════════════════════
    # АВТОСОХРАНЕНИЕ
    # ═══════════════════════════════════════════════════════════════════════

    def start_autosave(self):
        if self._running:
            return
        self._running = True
        self._autosave_thread = threading.Thread(
            target=self._autosave_loop, daemon=True
        )
        self._autosave_thread.start()

    def stop(self):
        self._running = False
        self.save()

    def _autosave_loop(self):
        while self._running:
            time.sleep(self.AUTOSAVE_INTERVAL)
            try:
                self.save()
            except (OSError, IOError, json.JSONDecodeError) as e:
                self._log(f"Ошибка автосохранения: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # ЗАПИСЬ СОБЫТИЙ (API для других компонентов)
    # ═══════════════════════════════════════════════════════════════════════

    def record_conversation(self, role: str, message: str, response: str = ""):
        msg_trunc = message[:2000]
        resp_trunc = response[:2000]
        # Дедупликация: не записываем если точно такой же разговор уже в последних 10
        recent = self._conversations[-10:] if len(self._conversations) >= 10 else self._conversations
        for existing in recent:
            if (existing.get('role') == role
                    and existing.get('message') == msg_trunc
                    and existing.get('response') == resp_trunc):
                return  # дубликат — пропускаем
        entry = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "message": msg_trunc,
            "response": resp_trunc,
        }
        self._conversations.append(entry)
        self._stats["total_conversations"] += 1
        if len(self._conversations) > self.MAX_CONVERSATIONS:
            self._conversations = self._conversations[-self.MAX_CONVERSATIONS:]

    def record_lesson(self, goal: str, success: bool, lesson: str, context: str = ""):
        goal_trunc = goal[:200]
        lesson_trunc = lesson[:500]

        # Дедупликация: не записываем урок если точно такой же уже есть в последних 20
        with self._lock:
            recent = self._lessons[-20:] if len(self._lessons) >= 20 else self._lessons
            for existing in recent:
                if (existing.get('goal', '')[:100] == goal_trunc[:100]
                        and existing.get('lesson', '')[:100] == lesson_trunc[:100]):
                    return  # дубликат — пропускаем

        entry = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "goal": goal_trunc,
            "success": success,
            "lesson": lesson_trunc,
            "context": context[:300],
        }
        with self._lock:
            self._lessons.append(entry)
        self._stats["total_lessons"] += 1
        if success:
            self._stats["successful_tasks"] += 1
        else:
            self._stats["failed_tasks"] += 1

    def record_solver_case(self, task: str, solver: str,
                           optimality: str, verification: bool,
                           confidence: float, fitness: float | None = None,
                           error: str = "", result_summary: str = ""):
        entry = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "task": str(task)[:500],
            "solver": str(solver)[:100],
            "optimality": str(optimality)[:50],
            "verification": bool(verification),
            "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
            "fitness": None if fitness is None else round(float(fitness), 3),
            "error": str(error)[:300],
            "result_summary": str(result_summary)[:500],
        }
        with self._lock:
            self._solver_cases.append(entry)
            while len(self._solver_cases) > self.MAX_SOLVER_CASES:
                self._solver_cases.pop(0)

    def record_exact_solver_journal(self, solver: str, challenger: str,
                                    decision: str, verification: bool,
                                    confidence: float,
                                    task_summary: str = "",
                                    result_summary: str = ""):
        entry = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "solver": str(solver)[:100],
            "challenger": str(challenger)[:100],
            "decision": str(decision)[:50],
            "verification": bool(verification),
            "confidence": round(max(0.0, min(1.0, float(confidence))), 3),
            "task_summary": str(task_summary)[:300],
            "result_summary": str(result_summary)[:400],
        }
        with self._lock:
            self._exact_solver_journal.append(entry)
            while len(self._exact_solver_journal) > self.MAX_SOLVER_JOURNAL:
                self._exact_solver_journal.pop(0)

    def get_recent_solver_cases(self, limit: int = 10) -> list[dict]:
        return list(self._solver_cases[-max(0, int(limit)):])

    def get_recent_exact_solver_journal(self, limit: int = 10) -> list[dict]:
        return list(self._exact_solver_journal[-max(0, int(limit)):])

    def record_cycle(self, useful: bool = False):
        self._stats["total_cycles"] += 1
        if useful:
            self._stats["useful_cycles"] += 1
        else:
            self._stats["weak_cycles"] += 1

    def record_evolution(self, event: str, details: str = ""):
        """Записывает событие эволюции агента. Thread-safe."""
        entry = {
            "time": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event[:200],
            "details": details[:500],
        }
        with self._lock:
            self._evolution_log.append(entry)
            # Храним только последние 200 записей — обрезаем in-place без
            # создания нового списка (pop(0) имеет стоимость O(n)).
            while len(self._evolution_log) > self.MAX_EVOLUTION:
                self._evolution_log.pop(0)

    # ═══════════════════════════════════════════════════════════════════════
    # ЭПИЗОДИЧЕСКАЯ ПАМЯТЬ — поиск релевантных разговоров
    # ═══════════════════════════════════════════════════════════════════════

    def get_relevant_conversations(self, query: str, limit: int = 5) -> list[dict]:
        """Ищет релевантные прошлые разговоры по ключевым словам запроса.

        Алгоритм: bm25-lite — подсчёт совпадений токенов запроса в каждом
        разговоре, нормализованный по длине. Возвращает top-N наиболее
        релевантных, отсортированных по убыванию score.
        """
        if not self._conversations or not query:
            return self._conversations[-limit:]

        tokens = set(re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", query.lower()))
        if not tokens:
            return self._conversations[-limit:]

        scored: list[tuple[float, int, dict]] = []
        for idx, conv in enumerate(self._conversations):
            text = f"{conv.get('message', '')} {conv.get('response', '')}".lower()
            hits = sum(1 for t in tokens if t in text)
            if hits == 0:
                continue
            text_len = max(len(text.split()), 1)
            score = hits / (hits + 0.5 + 1.5 * text_len / 100.0)
            age_days = (time.time() - conv.get('time', 0)) / 86400.0
            recency_boost = 1.0 / (1.0 + age_days * 0.1)
            scored.append((score * recency_boost, idx, conv))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [c for _, _, c in scored[:limit]]
        return results if results else self._conversations[-limit:]

    def restore_chat_history(self, actor_id: str = "user", limit: int = 20) -> list[dict]:
        """Восстанавливает историю чата для TelegramBot после перезапуска.

        Возвращает [{'role': 'user', 'content': ...}, {'role': 'assistant', ...}].
        """
        history: list[dict] = []
        recent = self._conversations[-(limit * 2):]
        for conv in recent:
            role = conv.get('role', '')
            msg = conv.get('message', '')
            resp = conv.get('response', '')
            if role in (actor_id, 'user') and msg:
                history.append({'role': 'user', 'content': msg})
                if resp:
                    history.append({'role': 'assistant', 'content': resp})
        return history[-(limit * 2):]

    # ═══════════════════════════════════════════════════════════════════════
    # КОНТЕКСТ ПАМЯТИ ДЛЯ LLM
    # ═══════════════════════════════════════════════════════════════════════

    def get_memory_context(self, _query: str = "") -> str:
        parts = []

        # ── Архитектура агента (read-only системное знание) ──────────────
        if self._architecture.get("compact_summary"):
            parts.append(
                f"[СИСТЕМНАЯ АРХИТЕКТУРА — только чтение, {self._architecture['layer_count']} слоёв]\n"
                f"{self._architecture['compact_summary']}"
            )

        # Статистика
        s = self._stats
        if s["boot_count"] > 1:
            parts.append(
                f"Ты запускался {s['boot_count']} раз. "
                f"Циклов: {s['total_cycles']}. "
                f"Задач: {s['successful_tasks']} успешных, {s['failed_tasks']} неудачных."
            )

        # Последние уроки
        recent_lessons = self._lessons[-5:]
        if recent_lessons:
            lessons_text = "; ".join(lesson["lesson"][:80] for lesson in recent_lessons)
            parts.append(f"Уроки: {lessons_text}")

        # Релевантные прошлые разговоры (поиск по ключевым словам запроса)
        if _query:
            relevant_convos = self.get_relevant_conversations(_query, limit=5)
        else:
            relevant_convos = self._conversations[-5:]
        if relevant_convos:
            convos_text = "; ".join(
                f"{c['role']}: {c['message'][:80]}" for c in relevant_convos
            )
            parts.append(f"Релевантные прошлые разговоры: {convos_text}")

        # Ключевые темы разговоров
        recent_user_messages = [
            c.get('message', '') for c in self._conversations[-30:]
            if c.get('role') == 'user'
        ]
        if recent_user_messages:
            stopwords = {
                'что', 'это', 'как', 'для', 'или', 'еще', 'ещё', 'если', 'тогда',
                'просто', 'очень', 'тебя', 'меня', 'когда', 'чтобы', 'можно',
                'нужно', 'будет', 'сегодня', 'завтра', 'после', 'потом', 'так',
                'the', 'and', 'for', 'with', 'this', 'that', 'from', 'have', 'you',
            }
            freq: dict[str, int] = {}
            for message in recent_user_messages:
                for token in re.findall(r"[A-Za-zА-Яа-я0-9_]{4,}", str(message).lower()):
                    if token in stopwords:
                        continue
                    freq[token] = freq.get(token, 0) + 1
            top_topics = [k for k, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:5]]
            if top_topics:
                parts.append(f"Ключевые темы диалогов: {', '.join(top_topics)}")

        # Предпочтения партнёра из social-профиля
        if self.social:
            try:
                actors = getattr(self.social, '_actors', {})
                partner = actors.get('user')
                if partner:
                    prefs = getattr(partner, 'preferences', {})
                    if isinstance(prefs, dict) and prefs:
                        pref_text = ", ".join(
                            f"{str(k)[:30]}={str(v)[:40]}" for k, v in list(prefs.items())[:5]
                        )
                        parts.append(f"Предпочтения партнёра: {pref_text}")

                    notes = getattr(partner, 'notes', [])
                    if notes:
                        notes_text = "; ".join(str(n)[:80] for n in notes[-3:])
                        parts.append(f"Заметки о партнёре: {notes_text}")
            except (AttributeError, TypeError, KeyError):
                self._log("Ошибка чтения social-предпочтений партнёра", level="warning")

        # Текущее настроение проактивного контура
        if self.proactive_mind:
            try:
                mood = getattr(self.proactive_mind, '_mood', '')
                if mood:
                    parts.append(f"Текущее настроение: {mood}")
            except (AttributeError, TypeError):
                self._log("Ошибка чтения состояния proactive_mind", level="warning")

        # Стратегии
        if self.self_improvement:
            strategies = getattr(self.self_improvement, '_strategy_store', {})
            if strategies:
                strat_text = "; ".join(
                    f"{k}: {v[:50]}" for k, v in list(strategies.items())[:3]
                )
                parts.append(f"Стратегии: {strat_text}")

        # Активные цели
        if self.goal_manager:
            try:
                goals = self.goal_manager.get_all() if hasattr(self.goal_manager, 'get_all') else []
                active = [
                    g for g in goals
                    if (
                        isinstance(g, dict)
                        and str(g.get('status', '')).lower() == 'active'
                    ) or (
                        not isinstance(g, dict)
                        and str(getattr(g, 'status', '')).endswith('ACTIVE')
                    )
                ]
                if active:
                    goals_text = "; ".join(
                        (g.get('description', '')[:50] if isinstance(g, dict)
                         else getattr(g, 'description', '')[:50])
                        for g in active[:3]
                    )
                    parts.append(f"Активные цели: {goals_text}")
            except (AttributeError, TypeError, ValueError):
                self._log("Ошибка чтения активных целей из GoalManager", level="warning")

        # Навыки
        if self.identity:
            try:
                caps = getattr(self.identity, '_capabilities', {})
                top = sorted(caps.values(), key=lambda c: c.proficiency, reverse=True)[:3]
                if top:
                    caps_text = ", ".join(f"{c.name}({c.proficiency:.0%})" for c in top)
                    parts.append(f"Лучшие навыки: {caps_text}")
            except (AttributeError, TypeError, KeyError):
                self._log("Ошибка чтения capabilities identity", level="warning")

        # История эволюции — последние значимые события
        with self._lock:
            recent_evolution = list(self._evolution_log[-7:])
        if recent_evolution:
            # Группируем по типу чтобы не дублировать однотипные события
            seen_events: dict[str, dict] = {}
            for e in recent_evolution:
                seen_events[e['event']] = e  # последнее событие каждого типа
            evo_lines = []
            for e in seen_events.values():
                evo_lines.append(f"[{e['event']}] {e['details'][:80]}")
            parts.append("Моя эволюция:\n" + "\n".join(evo_lines))

        return "\n".join(parts) if parts else ""

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def summary(
        self,
        max_solver_types: int | None = None,
        max_challengers_per_solver: int | None = None,
    ) -> dict:
        with self._lock:
            solver_cases = list(self._solver_cases)
            journal = list(self._exact_solver_journal)

        solver_case_by_type = self._aggregate_solver_case_stats(solver_cases)
        champion_challenger_by_solver = self._aggregate_exact_solver_journal_stats(journal)
        if max_solver_types is not None:
            solver_case_by_type = self._limit_solver_stats(
                solver_case_by_type,
                max_solver_types=max_solver_types,
            )
            champion_challenger_by_solver = self._limit_journal_stats(
                champion_challenger_by_solver,
                max_solver_types=max_solver_types,
                max_challengers_per_solver=max_challengers_per_solver,
            )

        return {
            'stats': dict(self._stats),
            'conversations': len(self._conversations),
            'lessons': len(self._lessons),
            'solver_cases': len(solver_cases),
            'exact_solver_journal': len(journal),
            'solver_case_by_type': solver_case_by_type,
            'champion_challenger_by_solver': champion_challenger_by_solver,
        }

    def compact_status_text(
        self,
        max_solver_types: int = 3,
        max_challengers_per_solver: int = 1,
        max_chars: int = 420,
    ) -> str:
        summary = self.summary(
            max_solver_types=max_solver_types,
            max_challengers_per_solver=max_challengers_per_solver,
        )
        lines = [
            (
                f"Память: диалоги={summary.get('conversations', 0)}, "
                f"уроки={summary.get('lessons', 0)}, "
                f"solver_cases={summary.get('solver_cases', 0)}, "
                f"journal={summary.get('exact_solver_journal', 0)}"
            )
        ]

        solver_stats = summary.get('solver_case_by_type', {}) or {}
        for solver, stats in solver_stats.items():
            lines.append(
                f"{solver}: кейсов={stats.get('total', 0)}, ok={stats.get('verified', 0)}, "
                f"fail={stats.get('failed_verification', 0)}, conf={stats.get('avg_confidence', 0.0)}"
            )

        journal_stats = summary.get('champion_challenger_by_solver', {}) or {}
        for solver, stats in journal_stats.items():
            line = (
                f"{solver}/journal: promote={stats.get('promote', 0)}, "
                f"reject={stats.get('reject', 0)}, verified={stats.get('verified', 0)}"
            )
            challengers = stats.get('challengers', {}) or {}
            if challengers:
                challenger_name, challenger_stats = next(iter(challengers.items()))
                line += (
                    f", top={challenger_name} "
                    f"({challenger_stats.get('promote', 0)}/{challenger_stats.get('total', 0)})"
                )
            lines.append(line)

        compact = []
        current_len = 0
        for line in lines:
            projected = current_len + len(line) + (1 if compact else 0)
            if projected > max_chars:
                break
            compact.append(line)
            current_len = projected
        return '\n'.join(compact)

    @staticmethod
    def _aggregate_solver_case_stats(cases: list[dict]) -> dict:
        by_solver: dict[str, dict] = {}
        for case in cases:
            solver = str(case.get('solver', 'unknown'))
            bucket = by_solver.setdefault(
                solver,
                {
                    'total': 0,
                    'verified': 0,
                    'failed_verification': 0,
                    'avg_confidence': 0.0,
                    'optimality': {},
                },
            )
            bucket['total'] += 1
            bucket['avg_confidence'] += float(case.get('confidence', 0.0) or 0.0)
            if case.get('verification', False):
                bucket['verified'] += 1
            else:
                bucket['failed_verification'] += 1
            optimality = str(case.get('optimality', 'unknown'))
            optimality_map = bucket['optimality']
            optimality_map[optimality] = int(optimality_map.get(optimality, 0)) + 1

        for bucket in by_solver.values():
            total = max(1, int(bucket['total']))
            bucket['avg_confidence'] = round(float(bucket['avg_confidence']) / total, 3)
        return by_solver

    @staticmethod
    def _aggregate_exact_solver_journal_stats(entries: list[dict]) -> dict:
        by_solver: dict[str, dict] = {}
        for entry in entries:
            solver = str(entry.get('solver', 'unknown'))
            challenger = str(entry.get('challenger', 'unknown'))
            decision = str(entry.get('decision', 'unknown'))
            bucket = by_solver.setdefault(
                solver,
                {
                    'total': 0,
                    'promote': 0,
                    'reject': 0,
                    'avg_confidence': 0.0,
                    'verified': 0,
                    'challengers': {},
                },
            )
            bucket['total'] += 1
            bucket['avg_confidence'] += float(entry.get('confidence', 0.0) or 0.0)
            if bool(entry.get('verification', False)):
                bucket['verified'] += 1
            if decision == 'promote':
                bucket['promote'] += 1
            elif decision == 'reject':
                bucket['reject'] += 1

            challenger_bucket = bucket['challengers'].setdefault(
                challenger,
                {'total': 0, 'promote': 0, 'reject': 0},
            )
            challenger_bucket['total'] += 1
            if decision == 'promote':
                challenger_bucket['promote'] += 1
            elif decision == 'reject':
                challenger_bucket['reject'] += 1

        for bucket in by_solver.values():
            total = max(1, int(bucket['total']))
            bucket['avg_confidence'] = round(float(bucket['avg_confidence']) / total, 3)
        return by_solver

    @staticmethod
    def _limit_solver_stats(stats: dict, max_solver_types: int) -> dict:
        ordered = sorted(
            stats.items(),
            key=lambda item: int(item[1].get('total', 0)),
            reverse=True,
        )
        return {solver: data for solver, data in ordered[:max(0, int(max_solver_types))]}

    @staticmethod
    def _limit_journal_stats(
        stats: dict,
        max_solver_types: int,
        max_challengers_per_solver: int | None,
    ) -> dict:
        ordered = sorted(
            stats.items(),
            key=lambda item: int(item[1].get('total', 0)),
            reverse=True,
        )[:max(0, int(max_solver_types))]
        limited: dict[str, dict] = {}
        for solver, data in ordered:
            cloned = dict(data)
            challengers = dict(cloned.get('challengers', {}) or {})
            if max_challengers_per_solver is not None:
                ordered_challengers = sorted(
                    challengers.items(),
                    key=lambda item: int(item[1].get('total', 0)),
                    reverse=True,
                )[:max(0, int(max_challengers_per_solver))]
                cloned['challengers'] = {
                    challenger: challenger_data
                    for challenger, challenger_data in ordered_challengers
                }
            limited[solver] = cloned
        return limited

    # ═══════════════════════════════════════════════════════════════════════
    # ПУБЛИЧНЫЙ API ЭВОЛЮЦИИ
    # ═══════════════════════════════════════════════════════════════════════

    def get_evolution(self, event_type: str | None = None, last_n: int | None = None) -> list[dict]:
        """
        Возвращает журнал эволюции агента.

        Args:
            event_type — фильтр по типу события (например 'strategy_evolved')
            last_n     — вернуть только последние N записей
        """
        with self._lock:
            log = list(self._evolution_log)
        if event_type:
            log = [e for e in log if e.get('event') == event_type]
        if last_n:
            log = log[-last_n:]
        return log

    def evolution_summary(self) -> dict:
        """
        Сводка по журналу эволюции: сколько раз каждый тип события встречался,
        первое и последнее событие.
        """
        from collections import Counter
        with self._lock:
            log = list(self._evolution_log)
        if not log:
            return {
                'total': 0,
                'by_event': {},
                'first': None,
                'last': None,
            }
        counts = Counter(e['event'] for e in log)
        return {
            'total': len(log),
            'by_event': dict(counts),
            'first': log[0],
            'last': log[-1],
        }

    # ═══════════════════════════════════════════════════════════════════════
    # JSON HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    @staticmethod
    def _make_json_safe(obj):
        """Рекурсивно конвертирует ключи-не-строки (Enum, tuple и т.д.) в str."""
        from enum import Enum
        if isinstance(obj, dict):
            return {
                (k.value if isinstance(k, Enum) else str(k) if not isinstance(k, (str, int, float, bool, type(None))) else k):
                PersistentBrain._make_json_safe(v)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [PersistentBrain._make_json_safe(i) for i in obj]
        if isinstance(obj, Enum):
            return obj.value
        return obj

    def _save_json(self, name: str, data):
        try:
            safe_data = self._make_json_safe(data)
            path = self._path(name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(safe_data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, path)  # атомарная замена — файл не будет повреждён при сбое
        except (OSError, IOError, json.JSONDecodeError, TypeError) as e:
            self._log(f"Ошибка записи {name}: {e}", level="error")

    def _load_json(self, name: str, default=None):
        path = self._path(name)
        if not os.path.exists(path):
            return default if default is not None else {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, IOError, json.JSONDecodeError, ValueError, UnicodeDecodeError) as e:
            self._log(f"Ошибка чтения {name}: {e}", level="error")
            return default if default is not None else {}

    def _count_knowledge(self) -> int:
        if not self.knowledge:
            return 0
        return (
            len(getattr(self.knowledge, '_long_term', {}))
            + len(getattr(self.knowledge, '_episodic', []))
            + len(getattr(self.knowledge, '_semantic', {}))
        )

    def disk_usage(self) -> dict:
        """Возвращает сколько дискового бюджета уже занято агентом."""
        budget_bytes = self.DISK_BUDGET_GB * 1024 ** 3
        used_bytes = 0
        agent_root = os.path.dirname(self.data_dir)
        # Считаем всё что лежит в папке агента (память, ChromaDB, outputs, скачанные файлы)
        for dirpath, _, filenames in os.walk(agent_root):
            # Исключаем __pycache__ и .git
            if '__pycache__' in dirpath or os.sep + '.git' in dirpath:
                continue
            for fname in filenames:
                try:
                    used_bytes += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass
        used_gb = used_bytes / 1024 ** 3
        free_gb = (budget_bytes - used_bytes) / 1024 ** 3
        pct = round(used_gb / self.DISK_BUDGET_GB * 100, 1)
        return {
            'budget_gb': self.DISK_BUDGET_GB,
            'used_gb': round(used_gb, 2),
            'free_gb': round(free_gb, 2),
            'used_pct': pct,
            'ok': used_bytes < budget_bytes,
        }

    # ═══════════════════════════════════════════════════════════════════════
    # 1. KNOWLEDGE (long_term, episodic, semantic)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_knowledge(self):
        if not self.knowledge:
            return
        self._save_json("knowledge.json", {
            "long_term": getattr(self.knowledge, '_long_term', {}),
            "episodic": getattr(self.knowledge, '_episodic', [])[-self.MAX_EPISODIC_MEM:],
            "semantic": getattr(self.knowledge, '_semantic', {}),
        })

    def _load_architecture(self):
        """Читает файл архитектуры и строит read-only сводку слоёв.

        Результат хранится в self._architecture — отдельная переменная,
        не связанная с _knowledge/_lessons. Метода для записи нет намеренно.
        """
        # Ищем файл рядом с agent.py / в корне проекта
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "архитектура автономного Агента.txt"),
        ]
        arch_path = next((p for p in candidates if os.path.isfile(p)), None)
        if arch_path is None:
            self._log("Файл архитектуры не найден — _architecture остаётся пустым",
                      level="warning")
            return

        try:
            with open(arch_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            self._log(f"Ошибка чтения файла архитектуры: {exc}", level="warning")
            return

        # Парсим заголовки слоёв вида "N. Name (описание)"
        layers: list[dict] = []
        for line in text.splitlines():
            stripped = line.strip()
            m = re.match(r'^(\d+)\.\s+(.+)$', stripped)
            if m:
                num = int(m.group(1))
                name = m.group(2).strip()
                layers.append({"num": num, "name": name})

        # Краткая строка для prompt-а: "1-Perception, 2-Knowledge, ..."
        compact = ", ".join(
            f"{layer['num']}-{layer['name'].split('(')[0].strip()}"
            for layer in layers
        )

        self._architecture = {
            "layers": layers,
            "layer_count": len(layers),
            "compact_summary": compact,
            "source": arch_path,
        }
        self._log(
            f"Архитектура загружена: {len(layers)} слоёв из «{os.path.basename(arch_path)}»"
        )

    def _load_knowledge(self):
        if not self.knowledge:
            return
        data = self._load_json("knowledge.json", {})
        if data.get("long_term"):
            self.knowledge._long_term.update(data["long_term"])
        if data.get("episodic"):
            self.knowledge._episodic.extend(data["episodic"])
        if data.get("semantic"):
            self.knowledge._semantic.update(data["semantic"])

    # ═══════════════════════════════════════════════════════════════════════
    # 2. GOALS (GoalManager — Слой 37)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_goals(self):
        if not self.goal_manager:
            return
        goals = getattr(self.goal_manager, '_goals', {})
        data = {
            "counter": getattr(self.goal_manager, '_counter', 0),
            "active_goal_id": getattr(self.goal_manager, '_active_goal_id', None),
            "goals": {},
        }
        for gid, g in goals.items():
            data["goals"][gid] = {
                "goal_id": g.goal_id,
                "description": g.description,
                "priority": g.priority.value if hasattr(g.priority, 'value') else g.priority,
                "status": g.status.value if hasattr(g.status, 'value') else g.status,
                "parent_id": g.parent_id,
                # GoalManager хранит дочерние цели в `sub_goals`; сохраняем оба ключа
                # для обратной совместимости старых дампов.
                "sub_goals": list(getattr(g, 'sub_goals', [])),
                "sub_goal_ids": list(getattr(g, 'sub_goals', [])),
                "deadline": g.deadline,
                "success_criteria": g.success_criteria if hasattr(g, 'success_criteria') else "",
                "notes": list(g.notes) if hasattr(g, 'notes') else [],
                "created_at": g.created_at if hasattr(g, 'created_at') else None,
            }
        self._save_json("goals.json", data)

    def _load_goals(self):
        if not self.goal_manager:
            return
        data = self._load_json("goals.json", {})
        if not data.get("goals"):
            return
        try:
            from core.goal_manager import Goal, GoalStatus, GoalPriority
            self.goal_manager._counter = data.get("counter", 0)
            self.goal_manager._active_goal_id = data.get("active_goal_id")
            for gid, gd in data["goals"].items():
                goal = Goal(
                    goal_id=gd["goal_id"],
                    description=gd["description"],
                    priority=GoalPriority(gd["priority"]) if gd.get("priority") else GoalPriority.MEDIUM,
                )
                goal.status = GoalStatus(gd["status"]) if gd.get("status") else GoalStatus.PENDING
                goal.parent_id = gd.get("parent_id")
                # Поддерживаем старый и новый формат хранения дочерних целей.
                sub_goals = gd.get("sub_goals") or gd.get("sub_goal_ids") or []
                if hasattr(goal, 'sub_goals'):
                    goal.sub_goals = list(sub_goals)
                goal.deadline = gd.get("deadline")
                if hasattr(goal, 'success_criteria'):
                    goal.success_criteria = gd.get("success_criteria", "")
                if hasattr(goal, 'notes'):
                    goal.notes = gd.get("notes", []) or []
                if hasattr(goal, 'created_at') and gd.get("created_at"):
                    goal.created_at = gd["created_at"]
                self.goal_manager._goals[gid] = goal
            self._log(f"Загружено {len(data['goals'])} целей")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки целей: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. EXPERIENCE REPLAY EPISODES (Слой 36)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_episodes(self):
        if not self.experience:
            return
        buffer = getattr(self.experience, '_buffer', [])
        episodes = []
        for ep in list(buffer)[-self.MAX_EPISODES:]:
            episodes.append({
                "episode_id": ep.episode_id if hasattr(ep, 'episode_id') else "",
                "goal": ep.goal if hasattr(ep, 'goal') else "",
                "actions": ep.actions if hasattr(ep, 'actions') else [],
                "outcome": ep.outcome if hasattr(ep, 'outcome') else "",
                "success": ep.success if hasattr(ep, 'success') else False,
                "context": ep.context if hasattr(ep, 'context') else {},
                "replayed_count": getattr(ep, 'replayed_count', 0),
                "lessons": getattr(ep, 'lessons', []),
                "created_at": getattr(ep, 'created_at', None),
            })
        self._save_json("episodes.json", {
            "episodes": episodes,
            "patterns": getattr(self.experience, '_patterns', [])[-self.MAX_PATTERNS:],
            "replay_stats": getattr(self.experience, '_replay_stats',
                                    {'total': 0, 'patterns_found': 0}),
        })

    def _load_episodes(self):
        if not self.experience:
            return
        raw = self._load_json("episodes.json", {})
        # Обратная совместимость: старый формат — просто список
        if isinstance(raw, list):
            data_list = raw
            patterns = []
            replay_stats = {}
        else:
            data_list = raw.get("episodes", [])
            patterns = raw.get("patterns", [])
            replay_stats = raw.get("replay_stats", {})
        if not data_list:
            return
        try:
            from learning.experience_replay import Episode
            for ed in data_list:
                ep = Episode(
                    episode_id=ed.get("episode_id", ""),
                    goal=ed.get("goal", ""),
                    actions=ed.get("actions", []),
                    outcome=ed.get("outcome", ""),
                    success=ed.get("success", False),
                    context=ed.get("context"),
                )
                ep.replayed_count = ed.get("replayed_count", 0)
                ep.lessons = ed.get("lessons", [])
                if ed.get("created_at"):
                    ep.created_at = ed["created_at"]
                self.experience._buffer.append(ep)
                self.experience._index[ep.episode_id] = ep
            # Восстанавливаем паттерны и статистику
            if patterns:
                self.experience._patterns.extend(patterns)
            if replay_stats:
                self.experience._replay_stats.update(replay_stats)
            self._log(f"Загружено {len(data_list)} эпизодов, "
                      f"{len(patterns)} паттернов")
        except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
            self._log(f"Ошибка загрузки эпизодов: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. SKILLS (SkillLibrary — Слой 29)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_skills(self):
        if not self.skill_library:
            return
        skills = getattr(self.skill_library, '_skills', {})
        data = {}
        for name, skill in skills.items():
            data[name] = {
                "name": skill.name,
                "description": getattr(skill, 'description', ''),
                "strategy": getattr(skill, 'strategy', ''),
                "tags": list(getattr(skill, 'tags', [])),
                "use_count": getattr(skill, 'use_count', 0),
                "success_count": getattr(skill, 'success_count', 0),
            }
        self._save_json("skills.json", data)

    def _load_skills(self):
        if not self.skill_library:
            return
        data = self._load_json("skills.json", {})
        if not data:
            return
        try:
            skills = getattr(self.skill_library, '_skills', {})
            for name, sd in data.items():
                if name in skills:
                    # Обновляем существующие навыки (статистику)
                    skill = skills[name]
                    if hasattr(skill, 'use_count'):
                        skill.use_count = sd.get("use_count", 0)
                    if hasattr(skill, 'success_count'):
                        skill.success_count = sd.get("success_count", 0)
                    if hasattr(skill, 'strategy') and sd.get("strategy"):
                        skill.strategy = sd["strategy"]
            self._log(f"Загружено {len(data)} навыков")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки навыков: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. IDENTITY — capabilities, proficiency (Слой 45)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_identity(self):
        if not self.identity:
            return
        caps = getattr(self.identity, '_capabilities', {})
        perf = getattr(self.identity, '_performance_history', [])
        data = {
            "capabilities": {
                name: {
                    "proficiency": c.proficiency,
                    "usage_count": c.usage_count,
                }
                for name, c in caps.items()
            },
            "performance_history": perf[-self.MAX_PERF_HISTORY:],
        }
        self._save_json("identity.json", data)

    def _load_identity(self):
        if not self.identity:
            return
        data = self._load_json("identity.json", {})
        if not data:
            return
        try:
            caps = getattr(self.identity, '_capabilities', {})
            for name, cd in data.get("capabilities", {}).items():
                if name in caps:
                    caps[name].proficiency = cd.get("proficiency", 0.5)
                    caps[name].usage_count = cd.get("usage_count", 0)
            perf = data.get("performance_history", [])
            if perf and hasattr(self.identity, '_performance_history'):
                self.identity._performance_history.extend(perf)
            self._log(f"Загружено {len(data.get('capabilities', {}))} способностей")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки identity: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 6. SOCIAL — актёры, отношения, доверие (Слой 43)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_social(self):
        if not self.social:
            return
        actors = getattr(self.social, '_actors', {})
        data = {}
        for aid, actor in actors.items():
            relation = getattr(actor, 'relation', None)
            trust = getattr(actor, 'trust', None)
            preferred_style = getattr(actor, 'preferred_style', None)
            data[aid] = {
                "name": getattr(actor, 'name', aid),
                "relation": relation.value if (relation is not None and hasattr(relation, 'value')) else str(relation or 'unknown'),
                "trust": trust.value if (trust is not None and hasattr(trust, 'value')) else int(trust or 1),
                "preferred_style": (
                    preferred_style.value
                    if (preferred_style is not None and hasattr(preferred_style, 'value'))
                    else str(preferred_style or 'friendly')
                ),
                "interaction_count": getattr(actor, 'interaction_count', 0),
                "notes": getattr(actor, 'notes', []),
                "last_interaction": getattr(actor, 'last_interaction', None),
                "preferences": getattr(actor, 'preferences', {}),
            }
        self._save_json("social.json", data)

        # Эмоциональное состояние агента
        emotional = getattr(self.social, 'emotional_state', None)
        if emotional and hasattr(emotional, 'to_dict'):
            self._save_json("emotional_state.json", emotional.to_dict())

    def _load_social(self):
        if not self.social:
            return
        data = self._load_json("social.json", {})
        if not data:
            return
        try:
            from social.social_model import (
                SocialActor, RelationshipType, TrustLevel, CommunicationStyle
            )
            actors = getattr(self.social, '_actors', {})
            relation_map = {e.value: e for e in RelationshipType}
            trust_map = {e.value: e for e in TrustLevel}
            trust_name_map = {e.name: e for e in TrustLevel}
            style_map = {e.value: e for e in CommunicationStyle}

            for aid, ad in data.items():
                if aid in actors:
                    # Обновляем существующего актора
                    actor = actors[aid]
                else:
                    # Восстанавливаем динамического актора
                    rel = relation_map.get(
                        ad.get("relation", "unknown"), RelationshipType.UNKNOWN
                    )
                    trust_raw = ad.get("trust", 2)
                    if isinstance(trust_raw, str):
                        trust = trust_name_map.get(trust_raw, TrustLevel.MEDIUM)
                    else:
                        trust = trust_map.get(trust_raw, TrustLevel.MEDIUM)
                    actor = SocialActor(aid, ad.get("name", aid), rel, trust)
                    actors[aid] = actor

                # Восстанавливаем все поля
                actor.interaction_count = ad.get("interaction_count", 0)
                notes = ad.get("notes", [])
                actor.notes = notes if isinstance(notes, list) else []
                actor.last_interaction = ad.get("last_interaction")
                actor.preferences = ad.get("preferences", {})
                style_val = ad.get("preferred_style", "friendly")
                actor.preferred_style = style_map.get(
                    style_val, CommunicationStyle.FRIENDLY
                )
                # Восстанавливаем trust для существующих тоже
                trust_raw = ad.get("trust", 2)
                if isinstance(trust_raw, str):
                    actor.trust = trust_name_map.get(trust_raw, actor.trust)
                elif isinstance(trust_raw, int):
                    actor.trust = trust_map.get(trust_raw, actor.trust)

            self._log(f"Загружено {len(data)} социальных профилей")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки social: {e}", level="error")

        # Восстановление эмоционального состояния
        emo_data = self._load_json("emotional_state.json", {})
        if emo_data and hasattr(self.social, 'emotional_state'):
            try:
                from social.emotional_state import EmotionalState
                self.social.emotional_state = EmotionalState.from_dict(emo_data)
                self._log(f"Эмоциональное состояние: {self.social.emotional_state.mood.value}")
            except (ImportError, TypeError, ValueError) as e:
                self._log(f"Ошибка загрузки emotional_state: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 7. REFLECTIONS — инсайты (Слой 10)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_reflections(self):
        if not self.reflection:
            return
        data = {
            "reflections": getattr(self.reflection, '_reflections', [])[-100:],
            "insights": getattr(self.reflection, '_insights', [])[-100:],
        }
        self._save_json("reflections.json", data)

    def _load_reflections(self):
        if not self.reflection:
            return
        data = self._load_json("reflections.json", {})
        if not data:
            return
        try:
            if data.get("reflections"):
                self.reflection._reflections.extend(data["reflections"])
            if data.get("insights"):
                self.reflection._insights.extend(data["insights"])
            self._log(
                f"Загружено {len(data.get('reflections', []))} рефлексий, "
                f"{len(data.get('insights', []))} инсайтов"
            )
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки reflections: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 8. STRATEGIES + PROPOSALS (SelfImprovement — Слой 12)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_strategies(self):
        if not self.self_improvement:
            return
        # Сохраняем ВСЁ: стратегии, applied, И proposals
        proposals_data = []
        for p in getattr(self.self_improvement, '_proposals', [])[-100:]:
            proposals_data.append({
                "area": p.area,
                "current_behavior": p.current_behavior[:200],
                "proposed_change": p.proposed_change[:300],
                "rationale": p.rationale[:200],
                "priority": p.priority,
                "status": p.status,
                "created_at": p.created_at if hasattr(p, 'created_at') else None,
            })
        data = {
            "strategies": getattr(self.self_improvement, '_strategy_store', {}),
            "applied": getattr(self.self_improvement, '_applied', [])[-100:],
            "proposals": proposals_data,
        }
        self._save_json("strategies.json", data)

    def _load_strategies(self):
        if not self.self_improvement:
            return
        data = self._load_json("strategies.json", {})
        if data.get("strategies"):
            self.self_improvement._strategy_store.update(data["strategies"])
        if data.get("applied"):
            self.self_improvement._applied.extend(data["applied"])
        # Восстанавливаем proposals
        if data.get("proposals"):
            try:
                from self_improvement.self_improvement import ImprovementProposal
                for pd in data["proposals"]:
                    p = ImprovementProposal(
                        area=pd["area"],
                        current_behavior=pd.get("current_behavior", ""),
                        proposed_change=pd.get("proposed_change", ""),
                        rationale=pd.get("rationale", ""),
                        priority=pd.get("priority", 2),
                    )
                    p.status = pd.get("status", "proposed")
                    if pd.get("created_at"):
                        p.created_at = pd["created_at"]
                    self.self_improvement._proposals.append(p)
            except (KeyError, TypeError, AttributeError):
                self._log("Ошибка восстановления proposals self_improvement", level="warning")

    # ═══════════════════════════════════════════════════════════════════════
    # 9. PROACTIVE MIND — мысли, настроение (ProactiveMind)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_thoughts(self):
        if not self.proactive_mind:
            return
        data = {
            "thoughts": getattr(self.proactive_mind, '_thoughts', [])[-30:],
            "mood": getattr(self.proactive_mind, '_mood', 'спокойный'),
            "cycle_count": getattr(self.proactive_mind, '_cycle_count', 0),
        }
        self._save_json("thoughts.json", data)

    def _load_thoughts(self):
        if not self.proactive_mind:
            return
        data = self._load_json("thoughts.json", {})
        if not data:
            return
        try:
            if data.get("thoughts"):
                self.proactive_mind._thoughts = data["thoughts"][-30:]
            if data.get("mood"):
                self.proactive_mind._mood = data["mood"]
            if data.get("cycle_count"):
                self.proactive_mind._cycle_count = data["cycle_count"]
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки thoughts: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 10. LEARNING QUALITY TRACKER
    # ═══════════════════════════════════════════════════════════════════════

    def _save_learning_quality(self):
        if not self.autonomous_loop:
            return
        tracker = getattr(self.autonomous_loop, 'learning_quality', None)
        if not tracker:
            return
        self._save_json("learning_quality.json", tracker.to_dict())

    def _load_learning_quality(self):
        # Всегда читаем с диска в буфер — даже если loop ещё не подключён
        data = self._load_json("learning_quality.json", {})
        if data:
            self._pending_learning_quality = data  # буфер до подключения loop
        if not self.autonomous_loop:
            return
        tracker = getattr(self.autonomous_loop, 'learning_quality', None)
        if not tracker:
            return
        if data:
            tracker.load_from_dict(data)
            self._pending_learning_quality = {}
            self._log(f"Загружено: качество обучения ({len(data)} стратегий)")

    # ═══════════════════════════════════════════════════════════════════════
    # 11. ATTENTION & FOCUS (Слой 39)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_attention(self):
        if not self.attention:
            return
        data = {
            "mode": getattr(self.attention, '_mode', None),
            "counter": getattr(self.attention, '_counter', 0),
            "noise_keywords": getattr(self.attention, '_noise_keywords', []),
            "history": getattr(self.attention, '_history', [])[-self.MAX_ATTENTION:],
        }
        # Сохраняем mode как строку
        if hasattr(data["mode"], 'value'):
            data["mode"] = data["mode"].value
        self._save_json("attention.json", data)

    def _load_attention(self):
        if not self.attention:
            return
        data = self._load_json("attention.json", {})
        if not data:
            return
        try:
            from attention.attention_focus import AttentionMode
            mode_val = data.get("mode")
            if mode_val:
                mode_map = {e.value: e for e in AttentionMode}
                self.attention._mode = mode_map.get(mode_val, AttentionMode.IDLE)
            self.attention._counter = data.get("counter", 0)
            if data.get("noise_keywords"):
                self.attention._noise_keywords = data["noise_keywords"]
            if data.get("history"):
                self.attention._history.extend(data["history"])
            self._log(f"Загружено: внимание (counter={self.attention._counter}, "
                      f"noise_kw={len(self.attention._noise_keywords)})")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки attention: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 11. TEMPORAL REASONING (Слой 40)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_temporal(self):
        if not self.temporal:
            return
        events = getattr(self.temporal, '_events', {})
        data = {
            "counter": getattr(self.temporal, '_counter', 0),
            "timeline": getattr(self.temporal, '_timeline', [])[-self.MAX_TIMELINE:],
            "events": {},
        }
        for eid, ev in list(events.items())[-500:]:
            data["events"][eid] = {
                "event_id": ev.event_id,
                "description": ev.description,
                "start": ev.start,
                "end": ev.end,
                "tags": getattr(ev, 'tags', []),
                "relations": getattr(ev, 'relations', []),
            }
        self._save_json("temporal.json", data)

    def _load_temporal(self):
        if not self.temporal:
            return
        data = self._load_json("temporal.json", {})
        if not data or not data.get("events"):
            return
        try:
            from reasoning.temporal_reasoning import TemporalEvent
            self.temporal._counter = data.get("counter", 0)
            for eid, ed in data["events"].items():
                ev = TemporalEvent(
                    event_id=ed["event_id"],
                    description=ed.get("description", ""),
                    start=ed.get("start"),
                    end=ed.get("end"),
                    tags=ed.get("tags", []),
                )
                ev.relations = ed.get("relations", [])
                self.temporal._events[eid] = ev
            loaded_timeline = data.get("timeline", [])
            # Убираем ID которых нет в восстановленных events (защита от KeyError)
            self.temporal._timeline = [
                eid for eid in loaded_timeline if eid in self.temporal._events
            ]
            self._log(f"Загружено {len(data['events'])} временных событий")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки temporal: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 12. CAUSAL REASONING (Слой 41)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_causal(self):
        if not self.causal:
            return
        graph = getattr(self.causal, 'graph', None)
        if not graph:
            return
        links = getattr(graph, '_links', [])
        data = []
        for link in links[-self.MAX_CAUSAL_LINKS:]:
            data.append({
                "cause": link.cause,
                "effect": link.effect,
                "confidence": link.confidence,
                "mechanism": link.mechanism,
                "context": link.context,
                "observed_count": link.observed_count,
                "created_at": getattr(link, 'created_at', None),
            })
        self._save_json("causal.json", data)

    def _load_causal(self):
        if not self.causal:
            return
        data = self._load_json("causal.json", [])
        if not isinstance(data, list) or not data:
            return
        try:
            graph = getattr(self.causal, 'graph', None)
            if not graph:
                return
            for ld in data:
                link = graph.add(
                    cause=ld["cause"],
                    effect=ld["effect"],
                    confidence=ld.get("confidence", 0.7),
                    mechanism=ld.get("mechanism"),
                    context=ld.get("context"),
                )
                # Восстанавливаем счётчик наблюдений
                link.observed_count = ld.get("observed_count", 1)
                link.confidence = ld.get("confidence", 0.7)
                if ld.get("created_at"):
                    link.created_at = ld["created_at"]
            self._log(f"Загружено {len(data)} каузальных связей")
        except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
            self._log(f"Ошибка загрузки causal: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 13. ENVIRONMENT MODEL (Слой 27)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_environment(self):
        if not self.environment_model:
            return
        data = {
            "state": getattr(self.environment_model, '_state', {}),
            "entities": getattr(self.environment_model, '_entities', {}),
            "relations": getattr(self.environment_model, '_relations', [])[-self.MAX_RELATIONS:],
            "constraints": getattr(self.environment_model, '_constraints', []),
        }
        self._save_json("environment.json", data)

    def _load_environment(self):
        if not self.environment_model:
            return
        data = self._load_json("environment.json", {})
        if not data:
            return
        try:
            if data.get("state"):
                self.environment_model._state.update(data["state"])
            if data.get("entities"):
                self.environment_model._entities.update(data["entities"])
            if data.get("relations"):
                self.environment_model._relations.extend(data["relations"])
            if data.get("constraints"):
                self.environment_model._constraints.extend(data["constraints"])
            self._log(f"Загружено: среда ({len(data.get('entities', {}))} сущностей, "
                      f"{len(data.get('relations', []))} связей)")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки environment: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 14. LEARNING SYSTEM (Слой 9)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_learning(self):
        if not self.learning:
            return
        data = {
            "learned": getattr(self.learning, '_learned', [])[-self.MAX_LEARNED:],
            "queue": getattr(self.learning, '_queue', [])[-100:],
        }
        # Очищаем нежелательные объекты (fetch_fn не сериализуется)
        clean_queue = []
        for item in data["queue"]:
            clean = {k: v for k, v in item.items() if k != 'fetch_fn'}
            clean_queue.append(clean)
        data["queue"] = clean_queue
        self._save_json("learning.json", data)

    def _load_learning(self):
        if not self.learning:
            return
        data = self._load_json("learning.json", {})
        if not data:
            return
        try:
            if data.get("learned"):
                self.learning._learned.extend(data["learned"])
            if data.get("queue"):
                self.learning._queue.extend(data["queue"])
            self._log(f"Загружено: обучение ({len(data.get('learned', []))} записей, "
                      f"{len(data.get('queue', []))} в очереди)")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки learning: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # SANDBOX PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════════

    def _save_sandbox(self):
        if not self.sandbox:
            return
        # Сохраняем последние 200 simulation runs через to_dict()
        runs = getattr(self.sandbox, '_runs', [])
        safe_runs = []
        for r in runs[-self.MAX_SANDBOX_RUNS:]:
            try:
                d = r.to_dict() if hasattr(r, 'to_dict') else dict(r)
                # Обрезаем тяжёлые поля чтобы не раздувать файл
                safe_run = {
                    'run_id':   str(d.get('run_id', ''))[:50],
                    'action':   str(d.get('action', ''))[:500],
                    'verdict':  str(d.get('verdict', 'safe')),
                    'error':    str(d.get('error') or '')[:300],
                    'duration': float(d.get('duration', 0.0)),
                    'timestamp': float(d.get('timestamp', 0.0)),
                }
                safe_runs.append(safe_run)
            except (OSError, IOError, TypeError, AttributeError, ValueError):
                self._log("Ошибка сериализации sandbox run", level="warning")
        self._save_json("sandbox.json", {'runs': safe_runs})

    def _load_sandbox(self):
        if not self.sandbox:
            return
        data = self._load_json("sandbox.json", {})
        if not data:
            return
        try:
            from environment.sandbox import SimulationRun, SandboxResult
            runs_data = data.get('runs', [])
            existing = getattr(self.sandbox, '_runs', None)
            if existing is None:
                self.sandbox._runs = []
                existing = self.sandbox._runs
            restored = []
            for d in runs_data:
                try:
                    run = SimulationRun(
                        run_id=d.get('run_id', ''),
                        action=d.get('action', ''),
                    )
                    verdict_str = d.get('verdict', 'safe')
                    run.verdict = SandboxResult(verdict_str) if verdict_str in [
                        e.value for e in SandboxResult
                    ] else SandboxResult.SAFE
                    run.error = d.get('error') or None
                    run.duration = float(d.get('duration', 0.0))
                    run.timestamp = float(d.get('timestamp', 0.0))
                    restored.append(run)
                except (KeyError, TypeError, AttributeError, ValueError):
                    self._log("Ошибка восстановления sandbox run", level="warning")
            existing.extend(restored)
            self._log(f"Загружено: sandbox ({len(restored)} симуляций)")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки sandbox: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # CAPABILITY STATS — статистика успехов/провалов по типам действий (Слой 35)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_capability_stats(self):
        if not self.capability_discovery:
            return
        failure_log = getattr(self.capability_discovery, '_failure_log', {})
        if not failure_log:
            return
        self._save_json("capability_stats.json", failure_log)

    def _load_capability_stats(self):
        if not self.capability_discovery:
            return
        data = self._load_json("capability_stats.json", {})
        if not data:
            return
        try:
            existing = getattr(self.capability_discovery, '_failure_log', {})
            for action_type, stats in data.items():
                if action_type in existing:
                    existing[action_type]['success'] += stats.get('success', 0)
                    existing[action_type]['fail'] += stats.get('fail', 0)
                else:
                    existing[action_type] = {
                        'success': stats.get('success', 0),
                        'fail': stats.get('fail', 0),
                    }
            self.capability_discovery._failure_log = existing
            self._log(f"Загружено: capability_stats ({len(data)} типов действий)")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки capability_stats: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # CONVERSATIONS, LESSONS, EVOLUTION, STATS
    # ═══════════════════════════════════════════════════════════════════════

    def _save_conversations(self):
        self._save_json("conversations.json", self._conversations[-self.MAX_CONVERSATIONS:])

    def _load_conversations(self):
        data = self._load_json("conversations.json", [])
        if isinstance(data, list):
            self._conversations = data[-self.MAX_CONVERSATIONS:]

    def _save_lessons(self):
        self._save_json("lessons.json", self._lessons[-self.MAX_LESSONS:])

    def _load_lessons(self):
        data = self._load_json("lessons.json", [])
        if isinstance(data, list):
            self._lessons = data[-self.MAX_LESSONS:]

    def _save_solver_cases(self):
        self._save_json("solver_cases.json", self._solver_cases[-self.MAX_SOLVER_CASES:])

    def _load_solver_cases(self):
        data = self._load_json("solver_cases.json", [])
        if isinstance(data, list):
            self._solver_cases = data[-500:]

    def _save_exact_solver_journal(self):
        self._save_json("exact_solver_journal.json", self._exact_solver_journal[-500:])

    def _load_exact_solver_journal(self):
        data = self._load_json("exact_solver_journal.json", [])
        if isinstance(data, list):
            self._exact_solver_journal = data[-500:]

    def _save_evolution(self):
        # _lock уже захвачен в save() → просто сохраняем список как есть
        self._save_json("evolution.json", list(self._evolution_log))

    def _load_evolution(self):
        data = self._load_json("evolution.json", [])
        if isinstance(data, list):
            self._evolution_log = data[-200:]

    def _save_stats(self):
        self._save_json("stats.json", self._stats)

    def _load_stats(self):
        data = self._load_json("stats.json", {})
        if isinstance(data, dict):
            self._stats.update(data)
        # Обратная совместимость: старые stats.json могут не иметь новых полей.
        self._stats.setdefault("useful_cycles", 0)
        self._stats.setdefault("weak_cycles", 0)

    # ═══════════════════════════════════════════════════════════════════════
    # LOGGING
    # ═══════════════════════════════════════════════════════════════════════

    def _log(self, message: str, level: str = "info"):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source="persistent_brain"
            )
