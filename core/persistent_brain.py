# PersistentBrain — Полная персистентная память агента
# Сохраняет и загружает ВСЁ состояние агента на диск.
# Ни один бит опыта не теряется при перезапуске.

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
        self._pending_failure_tracker: dict = {}
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

        self._lock = threading.RLock()  # RLock: предотвращаем deadlock при save→component→callback→brain
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
            self._load_failure_tracker()
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
            self._save_failure_tracker()
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
        with self._lock:
            if self._running:
                return
            self._running = True
        self._autosave_thread = threading.Thread(
            target=self._autosave_loop, daemon=True
        )
        self._autosave_thread.start()

    def stop(self):
        self._running = False
        if self._autosave_thread and self._autosave_thread.is_alive():
            self._autosave_thread.join(timeout=self.AUTOSAVE_INTERVAL + 5)
        self.save()

    def set_autonomous_loop(self, loop):
        """Подключает autonomous_loop и применяет отложенные буферы."""
        self.autonomous_loop = loop
        self._apply_pending_buffers()

    def _apply_pending_buffers(self):
        """Применяет данные из _pending_* буферов, если loop уже подключён."""
        if not self.autonomous_loop:
            return
        with self._lock:
            if self._pending_learning_quality:
                tracker = getattr(self.autonomous_loop, 'learning_quality', None)
                if tracker and hasattr(tracker, 'load_from_dict'):
                    tracker.load_from_dict(self._pending_learning_quality)
                    self._log(f"Применён отложенный learning_quality ({len(self._pending_learning_quality)} стратегий)")
                    self._pending_learning_quality = {}
            if self._pending_failure_tracker:
                ft = getattr(self.autonomous_loop, 'failure_tracker', None)
                if ft and hasattr(ft, 'load_from_dict'):
                    ft.load_from_dict(self._pending_failure_tracker)
                    self._log(f"Применён отложенный failure_tracker ({len(self._pending_failure_tracker.get('history', []))} записей)")
                    self._pending_failure_tracker = {}

    def _autosave_loop(self):
        while self._running:
            time.sleep(self.AUTOSAVE_INTERVAL)
            try:
                self._check_disk_budget()
                self.save()
            except (OSError, IOError) as e:
                self._log(f"Ошибка автосохранения: {e}", level="error")

    def _check_disk_budget(self):
        """Проверяет, не превышен ли дисковый бюджет DISK_BUDGET_GB."""
        try:
            total_bytes = 0
            for dirpath, _dirnames, filenames in os.walk(self.data_dir):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total_bytes += os.path.getsize(fp)
                    except OSError:
                        pass
            total_gb = total_bytes / (1024 ** 3)
            if total_gb > self.DISK_BUDGET_GB:
                self._log(
                    f"Дисковый бюджет превышен: {total_gb:.2f} GB > {self.DISK_BUDGET_GB} GB. "
                    "Автосохранение пропущено.",
                    level="error",
                )
                raise OSError("Disk budget exceeded")
        except OSError:
            raise
        except Exception:
            pass  # Не блокируем автосохранение из-за ошибки подсчёта

    # ═══════════════════════════════════════════════════════════════════════
    # ЗАПИСЬ СОБЫТИЙ (API для других компонентов)
    # ═══════════════════════════════════════════════════════════════════════

    def record_conversation(self, role: str, message: str, response: str = ""):
        msg_trunc = message[:2000]
        resp_trunc = response[:2000]
        with self._lock:
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

        with self._lock:
            # Дедупликация: не записываем урок если точно такой же уже есть в последних 20
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
            self._lessons.append(entry)
            self._stats["total_lessons"] += 1
            if success:
                self._stats["successful_tasks"] += 1
            else:
                self._stats["failed_tasks"] += 1
            if len(self._lessons) > self.MAX_LESSONS:
                self._lessons = self._lessons[-self.MAX_LESSONS:]

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
            if len(self._solver_cases) > self.MAX_SOLVER_CASES:
                self._solver_cases = self._solver_cases[-self.MAX_SOLVER_CASES:]

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
            if len(self._exact_solver_journal) > self.MAX_SOLVER_JOURNAL:
                self._exact_solver_journal = self._exact_solver_journal[-self.MAX_SOLVER_JOURNAL:]

    def get_recent_solver_cases(self, limit: int = 10) -> list[dict]:
        with self._lock:
            return list(self._solver_cases[-max(0, int(limit)):])

    def get_recent_exact_solver_journal(self, limit: int = 10) -> list[dict]:
        with self._lock:
            return list(self._exact_solver_journal[-max(0, int(limit)):])

    def record_cycle(self, useful: bool = False):
        with self._lock:
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
            # Храним только последние MAX_EVOLUTION записей
            if len(self._evolution_log) > self.MAX_EVOLUTION:
                self._evolution_log = self._evolution_log[-self.MAX_EVOLUTION:]

    # ═══════════════════════════════════════════════════════════════════════
    # ЭПИЗОДИЧЕСКАЯ ПАМЯТЬ — поиск релевантных разговоров
    # ═══════════════════════════════════════════════════════════════════════

    def get_relevant_conversations(self, query: str, limit: int = 5) -> list[dict]:
        """Ищет релевантные прошлые разговоры по ключевым словам запроса.

        Алгоритм: bm25-lite — подсчёт совпадений токенов запроса в каждом
        разговоре, нормализованный по длине. Возвращает top-N наиболее
        релевантных, отсортированных по убыванию score.
        """
        with self._lock:
            conversations = list(self._conversations)
        if not conversations or not query:
            return conversations[-limit:]

        tokens = set(re.findall(r"[A-Za-zА-Яа-я0-9_]{3,}", query.lower()))
        if not tokens:
            return conversations[-limit:]

        scored: list[tuple[float, int, dict]] = []
        for idx, conv in enumerate(conversations):
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
        return results if results else conversations[-limit:]

    def restore_chat_history(self, actor_id: str = "user", limit: int = 20) -> list[dict]:
        """Восстанавливает историю чата для TelegramBot после перезапуска.

        Возвращает [{'role': 'user', 'content': ...}, {'role': 'assistant', ...}].
        """
        with self._lock:
            recent = list(self._conversations[-(limit * 2):])
        history: list[dict] = []
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
        # Снимок мутабельных полей под локом
        with self._lock:
            architecture = dict(self._architecture)
            stats = dict(self._stats)
            lessons_snap = list(self._lessons[-5:])
            conversations_snap = list(self._conversations)
            evolution_snap = list(self._evolution_log[-7:])

        parts = []

        # ── Архитектура агента (read-only системное знание) ──────────────
        if architecture.get("compact_summary"):
            parts.append(
                f"[СИСТЕМНАЯ АРХИТЕКТУРА — только чтение, {architecture['layer_count']} слоёв]\n"
                f"{architecture['compact_summary']}"
            )

        # Статистика
        s = stats
        if s["boot_count"] > 1:
            parts.append(
                f"Ты запускался {s['boot_count']} раз. "
                f"Циклов: {s['total_cycles']}. "
                f"Задач: {s['successful_tasks']} успешных, {s['failed_tasks']} неудачных."
            )

        # Последние уроки
        recent_lessons = lessons_snap
        if recent_lessons:
            # Фильтруем: пропускаем мусорные уроки (stringified dicts)
            clean = [
                entry["lesson"][:80] for entry in recent_lessons
                if isinstance(entry.get("lesson"), str)
                and not entry["lesson"].lstrip().startswith("{")
                and len(entry["lesson"]) > 10
            ]
            if clean:
                lessons_text = "; ".join(clean)
                parts.append(f"Уроки: {lessons_text}")

        # Релевантные прошлые разговоры (поиск по ключевым словам запроса)
        if _query:
            relevant_convos = self.get_relevant_conversations(_query, limit=5)
        else:
            relevant_convos = conversations_snap[-5:]
        if relevant_convos:
            convos_text = "; ".join(
                f"{c['role']}: {c['message'][:80]}" for c in relevant_convos
            )
            parts.append(f"Релевантные прошлые разговоры: {convos_text}")

        # Ключевые темы разговоров
        recent_user_messages = [
            c.get('message', '') for c in conversations_snap[-30:]
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
                partner = self.social.get_actor('user')
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
                mood = self.proactive_mind.mood
                if mood:
                    parts.append(f"Текущее настроение: {mood}")
            except (AttributeError, TypeError):
                self._log("Ошибка чтения состояния proactive_mind", level="warning")

        # Стратегии
        if self.self_improvement:
            strategies = self.self_improvement.strategies
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
                top = self.identity.top_capabilities(3)
                if top:
                    caps_text = ", ".join(f"{c.name}({c.proficiency:.0%})" for c in top)
                    parts.append(f"Лучшие навыки: {caps_text}")
            except (AttributeError, TypeError, KeyError):
                self._log("Ошибка чтения capabilities identity", level="warning")

        # История эволюции — последние значимые события
        recent_evolution = evolution_snap
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

        with self._lock:
            stats_snap = dict(self._stats)
            conv_count = len(self._conversations)
            lesson_count = len(self._lessons)
        return {
            'stats': stats_snap,
            'conversations': conv_count,
            'lessons': lesson_count,
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
    def _make_json_safe(obj, _depth: int = 0):
        """Рекурсивно конвертирует ключи-не-строки (Enum, tuple и т.д.) в str."""
        if _depth > 50:
            return str(obj)
        from enum import Enum
        if isinstance(obj, dict):
            return {
                (k.value if isinstance(k, Enum) else str(k) if not isinstance(k, (str, int, float, bool, type(None))) else k):
                PersistentBrain._make_json_safe(v, _depth + 1)
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple)):
            return [PersistentBrain._make_json_safe(i, _depth + 1) for i in obj]
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
        except (OSError, IOError, TypeError) as e:
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
        return self.knowledge.knowledge_count()

    def disk_usage(self) -> dict:
        """Возвращает сколько дискового бюджета уже занято памятью агента."""
        budget_bytes = self.DISK_BUDGET_GB * 1024 ** 3
        used_bytes = 0
        # Считаем только data_dir (персистентная память), а не весь проект
        for dirpath, _, filenames in os.walk(self.data_dir):
            for fname in filenames:
                try:
                    used_bytes += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass
        used_gb = used_bytes / 1024 ** 3
        free_gb = max(0.0, (budget_bytes - used_bytes) / 1024 ** 3)
        pct = min(100.0, round(used_gb / self.DISK_BUDGET_GB * 100, 1))
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
        state = self.knowledge.export_state()
        trimmed = dict(state)
        trimmed["episodic"] = state.get("episodic", [])[-self.MAX_EPISODIC_MEM:]
        self._save_json("knowledge.json", trimmed)

    def _load_architecture(self):
        """Читает файл архитектуры и строит read-only сводку слоёв.

        Результат хранится в self._architecture — отдельная переменная,
        не связанная с _knowledge/_lessons. Метода для записи нет намеренно.
        """
        # Ищем файл рядом с agent.py / в корне проекта
        project_root = os.path.dirname(os.path.dirname(__file__))
        candidates = [
            os.path.join(project_root, "архитектура автономного Агента.txt"),
            os.path.join(project_root, "architecture.txt"),
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
        if data:
            self.knowledge.import_state(data)

    # ═══════════════════════════════════════════════════════════════════════
    # 2. GOALS (GoalManager — Слой 37)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_goals(self):
        if not self.goal_manager:
            return
        self._save_json("goals.json", self.goal_manager.export_state())

    def _load_goals(self):
        if not self.goal_manager:
            return
        data = self._load_json("goals.json", {})
        if not data.get("goals"):
            return
        try:
            self.goal_manager.import_state(data)
            self._log(f"Загружено {len(data['goals'])} целей")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки целей: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. EXPERIENCE REPLAY EPISODES (Слой 36)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_episodes(self):
        if not self.experience:
            return
        state = self.experience.export_state()
        trimmed = dict(state)
        trimmed["episodes"] = state.get("episodes", [])[-self.MAX_EPISODES:]
        trimmed["patterns"] = state.get("patterns", [])[-self.MAX_PATTERNS:]
        self._save_json("episodes.json", trimmed)

    def _load_episodes(self):
        if not self.experience:
            return
        raw = self._load_json("episodes.json", {})
        if isinstance(raw, list):
            data = raw
        else:
            data = raw
        if not data:
            return
        try:
            self.experience.import_state(data)
            count = len(raw) if isinstance(raw, list) else len(raw.get("episodes", []))
            patterns = 0 if isinstance(raw, list) else len(raw.get("patterns", []))
            self._log(f"Загружено {count} эпизодов, {patterns} паттернов")
        except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
            self._log(f"Ошибка загрузки эпизодов: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. SKILLS (SkillLibrary — Слой 29)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_skills(self):
        if not self.skill_library:
            return
        self._save_json("skills.json", self.skill_library.export_state())

    def _load_skills(self):
        if not self.skill_library:
            return
        data = self._load_json("skills.json", {})
        if not data:
            return
        try:
            self.skill_library.import_state(data)
            skills_count = len(data.get('skills', data) if isinstance(data, dict) else data)
            self._log(f"Загружено {skills_count} навыков")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки навыков: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. IDENTITY — capabilities, proficiency (Слой 45)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_identity(self):
        if not self.identity:
            return
        state = self.identity.export_state()
        trimmed = dict(state)
        trimmed["performance_history"] = state.get("performance_history", [])[-self.MAX_PERF_HISTORY:]
        self._save_json("identity.json", trimmed)

    def _load_identity(self):
        if not self.identity:
            return
        data = self._load_json("identity.json", {})
        if not data:
            return
        try:
            self.identity.import_state(data)
            self._log(f"Загружено {len(data.get('capabilities', {}))} способностей")
        except (KeyError, TypeError, AttributeError) as e:
            self._log(f"Ошибка загрузки identity: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 6. SOCIAL — актёры, отношения, доверие (Слой 43)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_social(self):
        if not self.social:
            return
        self._save_json("social.json", self.social.export_state())

    def _load_social(self):
        if not self.social:
            return
        data = self._load_json("social.json", {})
        if not data:
            return
        # Обратная совместимость: подтягиваем старый emotional_state.json
        # в новый формат, если social.json не содержит emotional_state
        if "emotional_state" not in data:
            emo_data = self._load_json("emotional_state.json", {})
            if emo_data:
                data["emotional_state"] = emo_data
        try:
            self.social.import_state(data)
            actors_count = len(data.get("actors", data))
            self._log(f"Загружено {actors_count} социальных профилей")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки social: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 7. REFLECTIONS — инсайты (Слой 10)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_reflections(self):
        if not self.reflection:
            return
        state = self.reflection.export_state()
        trimmed = dict(state)
        trimmed["reflections"] = state.get("reflections", [])[-100:]
        trimmed["insights"] = state.get("insights", [])[-100:]
        self._save_json("reflections.json", trimmed)

    def _load_reflections(self):
        if not self.reflection:
            return
        data = self._load_json("reflections.json", {})
        if not data:
            return
        try:
            self.reflection.import_state(data)
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
        state = self.self_improvement.export_state()
        trimmed = dict(state)
        trimmed["proposals"] = state.get("proposals", [])[-100:]
        trimmed["applied"] = state.get("applied", [])[-100:]
        self._save_json("strategies.json", trimmed)

    def _load_strategies(self):
        if not self.self_improvement:
            return
        data = self._load_json("strategies.json", {})
        if not data:
            return
        try:
            self.self_improvement.import_state(data)
        except (KeyError, TypeError, AttributeError):
            self._log("Ошибка восстановления strategies/proposals", level="warning")

    # ═══════════════════════════════════════════════════════════════════════
    # 9. PROACTIVE MIND — мысли, настроение (ProactiveMind)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_thoughts(self):
        if not self.proactive_mind:
            return
        state = self.proactive_mind.export_state()
        trimmed = dict(state)
        trimmed["thoughts"] = state.get("thoughts", [])[-30:]
        self._save_json("thoughts.json", trimmed)

    def _load_thoughts(self):
        if not self.proactive_mind:
            return
        data = self._load_json("thoughts.json", {})
        if not data:
            return
        try:
            self.proactive_mind.import_state(data)
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
    # 10b. FAILURE TRACKER (FailureTracker внутри AutonomousLoop)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_failure_tracker(self):
        if not self.autonomous_loop:
            return
        ft = getattr(self.autonomous_loop, 'failure_tracker', None)
        if not ft or not hasattr(ft, 'to_dict'):
            return
        self._save_json("failure_tracker.json", ft.to_dict())

    def _load_failure_tracker(self):
        data = self._load_json("failure_tracker.json", {})
        if not data:
            return
        self._pending_failure_tracker = data
        if not self.autonomous_loop:
            return
        ft = getattr(self.autonomous_loop, 'failure_tracker', None)
        if not ft or not hasattr(ft, 'load_from_dict'):
            return
        ft.load_from_dict(data)
        self._pending_failure_tracker = {}
        self._log(f"Загружено: failure tracker ({len(data.get('history', []))} записей)")

    # ═══════════════════════════════════════════════════════════════════════
    # 11. ATTENTION & FOCUS (Слой 39)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_attention(self):
        if not self.attention:
            return
        state = self.attention.export_state()
        trimmed = dict(state)
        trimmed["history"] = state.get("history", [])[-self.MAX_ATTENTION:]
        self._save_json("attention.json", trimmed)

    def _load_attention(self):
        if not self.attention:
            return
        data = self._load_json("attention.json", {})
        if not data:
            return
        try:
            self.attention.import_state(data)
            self._log(f"Загружено: внимание (counter={data.get('counter', 0)}, "
                      f"noise_kw={len(data.get('noise_keywords', []))})")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки attention: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 11. TEMPORAL REASONING (Слой 40)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_temporal(self):
        if not self.temporal:
            return
        state = self.temporal.export_state()
        trimmed = dict(state)
        trimmed["timeline"] = state.get("timeline", [])[-self.MAX_TIMELINE:]
        # Ограничиваем количество событий
        events = state.get("events", {})
        if len(events) > 500:
            keys = list(events.keys())[-500:]
            trimmed["events"] = {k: events[k] for k in keys}
        self._save_json("temporal.json", trimmed)

    def _load_temporal(self):
        if not self.temporal:
            return
        data = self._load_json("temporal.json", {})
        if not data:
            return
        try:
            self.temporal.import_state(data)
            self._log(f"Загружено {len(data.get('events', {}))} временных событий")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки temporal: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 12. CAUSAL REASONING (Слой 41)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_causal(self):
        if not self.causal:
            return
        links = self.causal.export_state()
        self._save_json("causal.json", {"links": links[-self.MAX_CAUSAL_LINKS:]})

    def _load_causal(self):
        if not self.causal:
            return
        raw = self._load_json("causal.json", {})
        # Обратная совместимость: старый формат — голый list
        if isinstance(raw, list):
            data = raw
        elif isinstance(raw, dict):
            data = raw.get("links", [])
        else:
            return
        if not data:
            return
        try:
            self.causal.import_state(data)
            self._log(f"Загружено {len(data)} каузальных связей")
        except (KeyError, TypeError, AttributeError, ValueError, IndexError) as e:
            self._log(f"Ошибка загрузки causal: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # 13. ENVIRONMENT MODEL (Слой 27)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_environment(self):
        if not self.environment_model:
            return
        state = self.environment_model.export_state()
        trimmed = dict(state)
        trimmed["relations"] = state.get("relations", [])[-self.MAX_RELATIONS:]
        self._save_json("environment.json", trimmed)

    def _load_environment(self):
        if not self.environment_model:
            return
        data = self._load_json("environment.json", {})
        if not data:
            return
        try:
            self.environment_model.import_state(data)
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
        state = self.learning.export_state()
        trimmed = dict(state)
        trimmed["learned"] = state.get("learned", [])[-self.MAX_LEARNED:]
        trimmed["queue"] = state.get("queue", [])[-100:]
        self._save_json("learning.json", trimmed)

    def _load_learning(self):
        if not self.learning:
            return
        data = self._load_json("learning.json", {})
        if not data:
            return
        try:
            self.learning.import_state(data)
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
        state = self.sandbox.export_state()
        trimmed = dict(state)
        trimmed["runs"] = state.get("runs", [])[-self.MAX_SANDBOX_RUNS:]
        self._save_json("sandbox.json", trimmed)

    def _load_sandbox(self):
        if not self.sandbox:
            return
        data = self._load_json("sandbox.json", {})
        if not data:
            return
        try:
            self.sandbox.import_state(data)
            self._log(f"Загружено: sandbox ({len(data.get('runs', []))} симуляций)")
        except (KeyError, TypeError, AttributeError, ValueError) as e:
            self._log(f"Ошибка загрузки sandbox: {e}", level="error")

    # ═══════════════════════════════════════════════════════════════════════
    # CAPABILITY STATS — статистика успехов/провалов по типам действий (Слой 35)
    # ═══════════════════════════════════════════════════════════════════════

    def _save_capability_stats(self):
        if not self.capability_discovery:
            return
        state = self.capability_discovery.export_state()
        if not state:
            return
        self._save_json("capability_stats.json", state)

    def _load_capability_stats(self):
        if not self.capability_discovery:
            return
        data = self._load_json("capability_stats.json", {})
        if not data:
            return
        try:
            self.capability_discovery.import_state(data)
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
            self._solver_cases = data[-self.MAX_SOLVER_CASES:]

    def _save_exact_solver_journal(self):
        self._save_json("exact_solver_journal.json", self._exact_solver_journal[-self.MAX_SOLVER_JOURNAL:])

    def _load_exact_solver_journal(self):
        data = self._load_json("exact_solver_journal.json", [])
        if isinstance(data, list):
            self._exact_solver_journal = data[-self.MAX_SOLVER_JOURNAL:]

    def _save_evolution(self):
        # _lock уже захвачен в save() → просто сохраняем список как есть
        self._save_json("evolution.json", list(self._evolution_log))

    def _load_evolution(self):
        data = self._load_json("evolution.json", [])
        if isinstance(data, list):
            self._evolution_log = data[-self.MAX_EVOLUTION:]

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
