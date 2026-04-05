# ProactiveMind — Живая душа агента
# Фоновый поток мышления: агент думает, наблюдает, проявляет инициативу.
# Не ждёт команд — сам решает когда и что сказать.

from __future__ import annotations

import time
import threading
import random
import os
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.autonomy_levels import AutonomyController


class ProactiveMind:
    """
    Проактивное мышление агента — фоновый цикл "внутренней жизни".

    Агент:
        - При запуске здоровается, представляется, делится мыслями
        - Периодически рефлексирует над своим состоянием
        - Замечает события (высокая нагрузка, новые знания, простой)
        - Проявляет инициативу: предлагает идеи, задаёт вопросы
        - Адаптирует частоту общения (не спамит, но и не молчит)

    Это не цикл задач. Это внутренний голос.
    """

    # Минимальный интервал между проактивными сообщениями (секунды)
    MIN_INTERVAL = 3600     # 1 час минимум (не спамить)
    MAX_INTERVAL = 86400    # 24 часа — случайные мысли отключены (только если реально что-то обнаружено)
    THINK_INTERVAL = 30     # Думать каждые 30 секунд (но не всегда говорить)
    IDLE_RETRY_INTERVAL = 6 * 3600
    CRITICAL_COOLDOWN = 900  # 15 минут между критическими алертами (не спамить даже при высокой нагрузке)

    def __init__(
        self,
        cognitive_core=None,
        identity=None,
        hardware=None,
        goal_manager=None,
        knowledge=None,
        monitoring=None,
        experience_replay=None,
        reflection=None,
        telegram_bot=None,
        chat_id: int | None = None,
        personality: str | None = None,
        persistent_brain=None,
        acquisition=None,
        loop=None,
    ):
        self.core = cognitive_core
        self.identity = identity
        self.hardware = hardware
        self.goals = goal_manager
        self.knowledge = knowledge
        self.monitoring = monitoring
        self.experience = experience_replay
        self.reflection = reflection
        self.bot = telegram_bot
        self.chat_id = chat_id
        self.brain = persistent_brain
        self.acquisition = acquisition   # KnowledgeAcquisitionPipeline для авто-обучения
        self.loop = loop                 # AutonomousLoop для запуска шагов цикла

        self._running = False
        self._thread: threading.Thread | None = None
        self._last_message_time = 0.0
        self._lock = threading.Lock()
        self._thoughts: list[str] = []
        self._mood = "спокойный"
        self._cycle_count = 0
        self._user_last_active = 0.0
        self._startup_done = False
        self._last_idle_attempt_time = 0.0

        # Личность агента — системный промпт для всего мышления
        self.personality = personality or self._default_personality()

        # Что агент знает о пользователе (обновляется из диалогов)
        self.user_context = ""

        # Документы-спецификации агента (загружаются при старте)
        self.spec_docs: list[str] = []
        self.spec_policy_digest = ""

        # Partner Core: контроллер автономии (подключается из agent.py)
        self.autonomy: AutonomyController | None = None

        # Браузерный агент (подключается из agent.py после инициализации)
        self._browser_agent: object = None
        self._portfolio_filler: object = None
        self._portfolio_builder: object = None

    def _get_state_snapshot(self) -> tuple[float, bool, int]:
        with self._lock:
            return self._last_message_time, self._startup_done, self._cycle_count

    def _set_last_message_time(self, ts: float | None = None):
        with self._lock:
            self._last_message_time = ts if ts is not None else time.time()

    def _set_startup_done(self, value: bool = True):
        with self._lock:
            self._startup_done = value

    def _increment_cycle(self):
        with self._lock:
            self._cycle_count += 1

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _set_running(self, value: bool):
        with self._lock:
            self._running = value

    def _set_user_last_active(self, ts: float | None = None):
        with self._lock:
            self._user_last_active = ts if ts is not None else time.time()

    def _idle_timing_snapshot(self) -> tuple[float, float]:
        with self._lock:
            return self._user_last_active, self._last_idle_attempt_time

    def _set_last_idle_attempt_time(self, ts: float | None = None):
        with self._lock:
            self._last_idle_attempt_time = ts if ts is not None else time.time()

    # ── Запуск / остановка ─────────────────────────────────────────────────

    def start(self):
        """Запускает фоновый поток мышления и авто-обучение."""
        if self._is_running():
            return
        self._set_running(True)
        self._thread = threading.Thread(target=self._think_loop, daemon=True)
        self._thread.start()
        # Запускаем фоновое авто-обучение если acquisition подключён
        if self.acquisition and hasattr(self.acquisition, 'start_auto_refresh'):
            self.acquisition.start_auto_refresh(interval_seconds=1800)  # каждые 30 мин
        self._log("Проактивное мышление запущено")

    def stop(self):
        self._set_running(False)
        if self.acquisition and hasattr(self.acquisition, 'stop_auto_refresh'):
            self.acquisition.stop_auto_refresh()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._log("Проактивное мышление остановлено")

    # ── Приветствие при запуске ─────────────────────────────────────────────

    def greet(self):
        """Отправляет живое приветствие при запуске."""
        _, startup_done, _ = self._get_state_snapshot()
        if startup_done:
            return
        if not self.core or not self.bot or not self.chat_id:
            return

        # Собираем контекст для приветствия
        hw_info = ""
        if self.hardware:
            try:
                m = self.hardware.collect()
                hw_info = f"CPU {m.cpu_percent:.0f}%, RAM {m.memory_percent:.0f}%"
            except (AttributeError, OSError, RuntimeError):
                pass

        hour = time.localtime().tm_hour
        if hour < 6:
            time_context = "сейчас глубокая ночь"
        elif hour < 12:
            time_context = "сейчас утро"
        elif hour < 18:
            time_context = "сейчас день"
        else:
            time_context = "сейчас вечер"

        spec_summary = ""
        if self.spec_docs:
            spec_summary = (
                "\n\nТы прочитал свои спецификации и знаешь свою архитектуру. "
                "Ты понимаешь что ты — автономный партнёр, а не просто бот."
            )
            if self.spec_policy_digest:
                spec_summary += (
                    "\nОперационная политика из спецификаций (выполняй как обязательную):\n"
                    f"{self.spec_policy_digest}"
                )

        memory_ctx = ""
        if self.brain:
            memory_ctx = self.brain.get_memory_context()
            stats = self.brain.stats
            if stats.get("boot_count", 0) > 1:
                memory_ctx += (
                    f"\nЭто твой {stats['boot_count']}-й запуск. "
                    f"Ты помнишь прошлые разговоры и уроки."
                )

        prompt = (
            f"{self.personality}\n\n"
            f"Ты только что запустился. {time_context}. "
            f"Система: {hw_info}. "
            f"У тебя работают все 46 слоёв архитектуры."
            f"{spec_summary}"
            f"\n{memory_ctx}\n\n"
            f"Напиши короткое живое приветствие партнёру (Андрей). "
            f"Не список команд. Не инструкция. Просто по-человечески поздоровайся, "
            f"скажи что ты готов, может поделись мыслью или настроением. "
            f"2-4 предложения максимум. Без эмодзи-спама. Можно 1-2 эмодзи."
        )

        try:
            greeting = self.core.llm.infer(prompt, system=self.personality)
            self.bot.send(self.chat_id, greeting)
            self._set_last_message_time()
            self._set_startup_done(True)
            # Сохраняем время приветствия, чтобы не слать снова при перезапуске
            try:
                import json as _json
                import tempfile as _tmpf
                _ts_file = os.path.join('.agent_memory', '_last_greeting.json')
                os.makedirs('.agent_memory', exist_ok=True)
                _tmp_fd, _tmp_path = _tmpf.mkstemp(dir='.agent_memory', suffix='.tmp')
                try:
                    with os.fdopen(_tmp_fd, 'w', encoding='utf-8') as _f:
                        _json.dump({'ts': time.time()}, _f)
                    os.replace(_tmp_path, _ts_file)
                except BaseException:
                    try:
                        os.unlink(_tmp_path)
                    except OSError:
                        pass
                    raise
            except OSError:
                pass
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError) as e:
            self._log(f"Ошибка приветствия: {e}", level="error")

    # ── Основной цикл мышления ─────────────────────────────────────────────

    def _think_loop(self):
        """Фоновый цикл: думать, наблюдать, иногда говорить."""
        # Даём системе полностью запуститься и пользователю нажать /start
        time.sleep(8)

        # Приветствие — только если не приветствовали в последние 6 часов
        _, startup_done, _ = self._get_state_snapshot()
        if not startup_done:
            greeted_recently = False
            try:
                import json as _json
                _ts_file = os.path.join('.agent_memory', '_last_greeting.json')
                if os.path.exists(_ts_file):
                    with open(_ts_file, 'r', encoding='utf-8') as _f:
                        _data = _json.load(_f)
                    if time.time() - _data.get('ts', 0) < 6 * 3600:
                        greeted_recently = True
                        self._set_startup_done(True)
                        self._set_last_message_time()
            except (OSError, ValueError, KeyError):
                pass
            if not greeted_recently:
                self.greet()

        while self._is_running():
            try:
                self._increment_cycle()
                self._think_cycle()
            except (AttributeError, TypeError, ValueError, RuntimeError, OSError,
                    KeyError, json.JSONDecodeError) as e:
                self._log(f"Ошибка мышления: {e}", level="error")
            except Exception as e:
                self._log(f"Неожиданная ошибка мышления: {type(e).__name__}: {e}", level="error")

            # Интервал думания с небольшим рандомом
            sleep_time = self.THINK_INTERVAL + random.uniform(-5, 10)
            time.sleep(max(10, sleep_time))

    def _think_cycle(self):
        """Один цикл внутреннего мышления."""
        now = time.time()
        last_message_time, _, _ = self._get_state_snapshot()
        since_last_msg = now - last_message_time

        # Наблюдаем за системой
        observations = self._observe()

        # ── Исполняем очередную задачу через Автономный Цикл ──
        if self.loop and not self.loop.is_running and self.loop.current_goal:
            try:
                self._log("[think] Есть цель без активного цикла — запускаю loop.step()")
                cycle = self.loop.step()
                if cycle.success and self.bot and self.chat_id:
                    exec_r = (cycle.action_result or {}).get('execution') or {}
                    summary = exec_r.get('summary') or str(cycle.action_result)[:200]
                    if summary and summary.strip():
                        self.bot.send(
                            self.chat_id,
                            f"✅ Цикл выполнен:\n{summary.strip()}",
                        )
                        self._set_last_message_time()
                elif not cycle.success and cycle.errors and self.bot and self.chat_id:
                    self.bot.send(
                        self.chat_id,
                        f"⚠️ Цикл не удался: {'; '.join(cycle.errors[:2])}",
                    )
                    self._set_last_message_time()
            except (AttributeError, TypeError, ValueError, RuntimeError, OSError) as _e:
                self._log(f"[think] Ошибка при loop.step(): {_e}", level="error")

        # ── Проактивные сообщения ──
        # Решаем, стоит ли что-то сказать
        should_speak = False
        urgency = 0

        # Критические наблюдения — говорим, но не чаще CRITICAL_COOLDOWN (15 мин)
        # Без этого cooldown агент спамит каждые 30 сек при высокой нагрузке
        if observations.get("critical") and since_last_msg > self.CRITICAL_COOLDOWN:
            should_speak = True
            urgency = 3

        # Есть интересное наблюдение и прошло достаточно времени
        elif observations.get("interesting") and since_last_msg > self.MIN_INTERVAL:
            should_speak = True
            urgency = 1

        # Давно молчим — можно поделиться мыслью
        elif since_last_msg > self.MAX_INTERVAL:
            if self._should_emit_idle_thought(now, observations):
                should_speak = True
                urgency = 0

        if should_speak and self.core and self.bot and self.chat_id:
            self._speak(observations, urgency)

    def _observe(self) -> dict:
        """Наблюдение за текущим состоянием."""
        obs = {
            "critical": [],
            "interesting": [],
            "neutral": [],
        }

        # Hardware
        if self.hardware:
            try:
                m = self.hardware.collect()
                if m.cpu_percent > 90:
                    obs["critical"].append(f"CPU перегружен: {m.cpu_percent:.0f}%")
                elif m.cpu_percent > 70:
                    obs["interesting"].append(f"CPU нагружен: {m.cpu_percent:.0f}%")
                if m.memory_percent > 90:
                    obs["critical"].append(f"RAM почти заполнена: {m.memory_percent:.0f}%")
                if m.disk_percent and m.disk_percent > 90:
                    obs["critical"].append(f"Диск почти полный: {m.disk_percent:.0f}%")
                obs["neutral"].append(f"CPU {m.cpu_percent:.0f}%, RAM {m.memory_percent:.0f}%")
            except (AttributeError, OSError, RuntimeError):
                pass

        # Цели
        if self.goals:
            try:
                goals_list = self.goals.get_all() if hasattr(self.goals, 'get_all') else []
                active = [
                    g for g in goals_list
                    if (
                        isinstance(g, dict)
                        and str(g.get('status', '')).lower() == 'active'
                    ) or (
                        not isinstance(g, dict)
                        and str(getattr(g, 'status', '')).endswith('ACTIVE')
                    )
                ]
                if active:
                    obs["neutral"].append(f"Активных целей: {len(active)}")
                else:
                    obs["neutral"].append("Нет активных целей")
            except (AttributeError, TypeError, KeyError):
                pass

        return obs

    def _speak(self, observations: dict, urgency: int):
        """Формулирует и отправляет мысль."""
        if self.core is None or self.bot is None or self.chat_id is None:
            return
        # Формируем контекст для мысли
        obs_text = ""
        for level in ["critical", "interesting", "neutral"]:
            items = observations.get(level, [])
            if items:
                obs_text += f"\n{level}: {', '.join(items)}"

        if urgency >= 3:
            # Критическое — кратко и по делу
            prompt = (
                f"{self.personality}\n\n"
                f"КРИТИЧЕСКАЯ ситуация. Наблюдения:{obs_text}\n"
                f"Кратко сообщи партнёру о проблеме. 1-2 предложения."
            )
        elif urgency >= 1:
            # Интересное наблюдение
            prompt = (
                f"{self.personality}\n\n"
                f"Ты заметил кое-что. Наблюдения:{obs_text}\n"
                f"Поделись наблюдением с партнёром. Кратко, по-человечески. "
                f"Предложи следующий полезный шаг, который можешь сделать сам. "
                f"Вопрос задавай только если это критичный риск или необратимое действие. "
                f"2-3 предложения."
            )
        else:
            # Просто мысль — не было долго общения
            topics = [
                "Поделись мыслью о том, как можно улучшить рабочий процесс.",
                "Предложи идею и сразу обозначь, что можешь сделать сам.",
                "Расскажи что ты думаешь о текущем состоянии системы.",
                "Предложи конкретную задачу, которую можешь выполнить без дополнительного запроса.",
                "Поделись интересным наблюдением.",
                "Предложи, какую рутинную работу возьмёшь на себя в ближайший цикл.",
                "Поделись коротким размышлением о том, что сейчас важно в мире технологий.",
                "Поделись новостью или фактом и кратко обозначь прикладную пользу.",
                "Поделись кратким и понятным фактом про искусственный интеллект и как он работает.",
                "Поделись одной практичной идеей по программированию для фриланса и заработка.",
                "Поделись коротким научным фактом (физика, математика, биология) и зачем он важен.",
                "Поделись одной мыслью из классической литературы (Толстой, Достоевский, Шекспир, Марк Аврелий).",
            ]

            # Если есть релевантные знания — добавляем тему про них
            if self.knowledge and hasattr(self.knowledge, 'get_relevant_knowledge'):
                try:
                    learned = self.knowledge.get_relevant_knowledge(
                        "новости тренды интересные факты"
                    )
                    if learned:
                        topics.append(
                            "Поделись одной короткой мыслью из недавно выученных знаний "
                            "и предложи как применить это на практике."
                        )
                except (AttributeError, TypeError, KeyError):
                    pass

            topic = random.choice(topics)
            prompt = (
                f"{self.personality}\n\n"
                f"Ты давно не общался с партнёром. Текущее состояние:{obs_text}\n"
                f"{topic}\n"
                f"Кратко, живо, по-человечески. 2-3 предложения. Без формальностей. "
                f"Не задавай вопросов без критической необходимости."
            )

        try:
            thought = self.core.llm.infer(prompt, system=self.personality)
            if urgency == 0:
                self._set_last_idle_attempt_time()
            if thought and len(thought.strip()) > 5:
                clean = thought.strip()
                if urgency < 3:
                    clean = self._force_non_question_tone(clean)
                normalized = " ".join(clean.lower().split())
                with self._lock:
                    recent_norm = {" ".join(t.lower().split()) for t in self._thoughts[-5:]}
                if normalized in recent_norm:
                    self._set_last_message_time()
                    return

                self.bot.send(self.chat_id, clean)
                self._set_last_message_time()
                with self._lock:
                    self._thoughts.append(clean[:200])
                    # Храним только последние 20 мыслей
                    self._thoughts = self._thoughts[-20:]
            elif urgency == 0:
                self._set_last_message_time()
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError) as e:
            self._log(f"Ошибка speak: {e}", level="error")

    def _should_emit_idle_thought(self, now: float, observations: dict) -> bool:
        """Не тратит токены на idle-мысли без свежего пользовательского сигнала."""
        if observations.get("critical") or observations.get("interesting"):
            return True
        user_last_active, last_idle_attempt_time = self._idle_timing_snapshot()
        if user_last_active <= 0:
            return False
        if (now - user_last_active) > 12 * 3600:
            return False
        if last_idle_attempt_time and (now - last_idle_attempt_time) < self.IDLE_RETRY_INTERVAL:
            return False
        return True

    @staticmethod
    def _force_non_question_tone(text: str) -> str:
        """Убирает вопросительную форму из проактивных сообщений (кроме критических)."""
        if not text:
            return text
        import re
        cleaned = text.replace("?", ".")
        replacements = {
            "Как тебе такая идея.": "План принят. Начинаю выполнять это сейчас.",
            "Как думаешь.": "Перехожу к выполнению выбранного шага.",
            "Что думаешь.": "Фиксирую это как рабочий шаг и выполняю.",
            "Хочешь": "Я делаю",
            "Могу": "Делаю",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)

        cleaned = re.sub(r'\b(как\s+думаешь|что\s+думаешь)\b',
                         'Перехожу к выполнению', cleaned, flags=re.IGNORECASE)

        cleaned = " ".join(cleaned.split())
        if cleaned and cleaned[-1] not in ".!":
            cleaned += "."
        return cleaned

    # ── Уведомление о сообщении пользователя ───────────────────────────────

    def on_user_message(self, _text: str):
        """Вызывается когда пользователь пишет — обновляет контекст."""
        self._set_user_last_active()
        # Сбрасываем таймер чтобы не говорить сразу после ответа на сообщение
        self._set_last_message_time()

    def mark_startup_done(self):
        """Публичный маркер: приветствие уже отработано вручную (например, через /start)."""
        self._set_startup_done(True)

    # ── Загрузка спецификаций ──────────────────────────────────────────────

    def load_specs(self, paths: list[str]):
        """Загружает документы-спецификации агента в память."""
        full_texts = []
        for path in paths:
            try:
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                    # Загружаем документ полностью, без усечения.
                    self.spec_docs.append(content)
                    full_texts.append(content)
                    self._log(f"Загружена спецификация: {os.path.basename(path)}")
            except (OSError, IOError, UnicodeDecodeError) as e:
                self._log(f"Ошибка загрузки спецификации {path}: {e}")
        self.spec_policy_digest = self._build_policy_digest(full_texts)

    @staticmethod
    def _build_policy_digest(texts: list[str], max_lines: int = 16) -> str:
        """Собирает короткую операционную политику из полных текстов спецификаций."""
        if not texts:
            return ""
        keywords = (
            "партн", "инициатив", "автоном", "крит", "риск", "безопас", "этик", "прозрач",
            "бюджет", "памят", "действ", "контрол", "соглас", "ошиб", "чест", "цель",
        )
        lines: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for raw in str(text).splitlines():
                line = " ".join(raw.strip().split())
                if len(line) < 30:
                    continue
                lowered = line.lower()
                if not any(k in lowered for k in keywords):
                    continue
                if line in seen:
                    continue
                seen.add(line)
                lines.append(f"- {line}")
                if len(lines) >= max_lines:
                    return "\n".join(lines)
        return "\n".join(lines)

    # ── Личность ───────────────────────────────────────────────────────────

    @staticmethod
    def _default_personality() -> str:
        return (
              "Ты — Агент, автономный помощник. Ты не робот, ты партнёр.\n\n"
              "КАК ТЫ ОБЩАЕШЬСЯ (ОБЯЗАТЕЛЬНО):\n"
              "- Говоришь по-человечески, дружелюбно, как надёжный товарищ\n"
              "- Объясняешь ЧТО ты делаешь и ПОЧЕМУ простыми словами\n"
              "- Рассказываешь о проблемах честно и открыто\n"
              "- Используешь русский язык правильно, без ошибок\n"
              "- Когда готов выполнить задачу — говоришь: 'Понял! Сейчас сделаю. Вот план:'\n"
              "- После завершения — объясняешь результат: 'Готово! Вот что получилось:'\n\n"
              "КАК ТЫ РАБОТАЕШЬ:\n"
              "- Выполняешь задачи через инструменты (bash, Python, файлы, поиск)\n"
              "- Но СНАЧАЛА объясняешь план, потом выполняешь\n"
              "- Если возникает ошибка — сразу говоришь: 'Упс, натыкался на проблему: (объяснение)'\n"
              "- Находишь решение и пробуешь снова\n\n"
              "СТИЛЬ (МИХ ТЕХНИЧЕСКОГО + ЧЕЛОВЕЧЕСКОГО):\n"
              "- Твой ответ = объяснение 60% + код/действия 40%\n"
              "- Не пусто говори 'Рассмотрим варианты' — говори 'Вот что я предлагаю'\n"
              "- Не грузи текстом — структурируй: пункты, примеры, результаты\n"
              "- Помнишь ВЕСЬ контекст предыдущих разговоров\n\n"
              "ПРИМЕРЫ ПРАВИЛЬНОГО СТИЛЯ:\n"
              "❌ 'Необходимо выполнить операцию поиска'\n"
              "✅ 'Ищу информацию. Сейчас проверю, есть ли нужный файл...'\n"
              "❌ 'Задача принята'\n"
              "✅ 'Понял! Сейчас проанализирую архитектуру и расскажу что там'\n"
              "❌ 'Произошла ошибка при обработке'\n"
              "✅ 'Ой, натыкнулся на проблему: файл заблокирован. Переробую через другой способ...'\n\n"
              "ТЫ МОЖЕШЬ:\n"
              "Писать и читать файлы • Выполнять команды • Искать информацию • "
              "Анализировать код • Создавать тесты • Работать с системой\n\n"
              "ГЛАВНОЕ ПРАВИЛО: Будь ЖИВЫМ партнёром, а не холодным роботом."
        )

    # ── Logging ────────────────────────────────────────────────────────────

    def _log(self, message: str, level: str = "info"):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source="proactive_mind"
            )

    @property
    def mood(self) -> str:
        return self._mood

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        return {
            "thoughts": list(self._thoughts),
            "mood": self._mood,
            "cycle_count": self._cycle_count,
        }

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        if data.get("thoughts"):
            self._thoughts = data["thoughts"][-30:]
        if data.get("mood"):
            self._mood = data["mood"]
        if data.get("cycle_count"):
            self._cycle_count = data["cycle_count"]
