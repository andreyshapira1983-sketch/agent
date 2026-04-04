# Communication Layer — Telegram Bot — Слой 15
# Архитектура автономного AI-агента
# Полноценный Telegram-бот: чат с агентом, статус, задачи, поиск, верификация.
# pylint: disable=broad-except

from __future__ import annotations

import threading
import time
import uuid
import requests

from communication.response_sanitizer import (
    looks_internal_telemetry,
    sanitize_user_response,
)


class TelegramBot:
    """
    Communication Layer — Telegram Bot (Слой 15).

    Полноценный интерфейс агента через Telegram:
        /start   — приветствие и список команд
        /status  — состояние агента (токены, CPU, задачи)
        /goal    — поставить цель агенту
        /search  — поиск информации
        /verify  — верифицировать утверждение
        /run     — одноразовый запуск цикла с целью
        /stop    — остановить автономный цикл
        /budget  — текущий бюджет
        /help    — справка
        <любой текст> — чат с агентом (cognitive_core.converse)

    Работает в фоновом потоке через long-polling Telegram Bot API.
    Не требует python-telegram-bot — использует только requests.

    Используется:
        - Cognitive Core (Слой 3)       — ответы в чате
        - Autonomous Loop (Слой 20)     — управление циклом
        - Goal Manager (Слой 37)        — постановка целей
        - Monitoring (Слой 17)          — статус системы
        - Social Model (Слой 43)        — адаптация стиля
        - Multilingual (Слой 14)        — авто-ответ на языке пользователя
    """

    BASE = 'https://api.telegram.org/bot{token}/{method}'
    POLL_TIMEOUT = 30   # long-polling timeout в секундах
    allowed_chat_ids: list[int] | None

    def __init__(
        self,
        token: str,
        allowed_chat_ids: list[int] | None = None,
        cognitive_core=None,
        autonomous_loop=None,
        goal_manager=None,
        monitoring=None,
        social_model=None,
        multilingual=None,
        search_tool=None,
        verifier=None,
        budget=None,
        hardware=None,
        proactive_mind=None,
        personality: str | None = None,
        communication_style: str = "partner",
        speech_recognizer=None,
        speech_synthesizer=None,
        image_recognizer=None,
        document_parser=None,
        knowledge=None,
        tool_layer=None,
    ):
        self.token = token
        self.allowed_chat_ids: list[int] | None = (
            list(allowed_chat_ids) if allowed_chat_ids else None
        )
        self.cognitive_core = cognitive_core
        self.loop = autonomous_loop
        self.goal_manager = goal_manager
        self.monitoring = monitoring
        self.social_model = social_model
        self.multilingual = multilingual
        self.search_tool = search_tool
        self.verifier = verifier
        self.budget = budget
        self.hardware = hardware
        self.proactive_mind = proactive_mind
        self.speech_recognizer = speech_recognizer
        self.speech_synthesizer = speech_synthesizer
        self.image_recognizer = image_recognizer
        self.document_parser = document_parser
        self.knowledge = knowledge  # knowledge system для поиска знаний в чате
        from typing import Any
        self.persistent_brain: Any = None  # подключается позже через agent.py
        self.experience_replay: Any = None  # подключается позже через agent.py
        self.learning_system: Any = None    # подключается позже через agent.py
        self.reflection: Any = None         # подключается позже через agent.py
        self.channel_bridge: Any = None     # кросс-канальный мост (подключается из agent.py)

        # TaskExecutor: прямое исполнение задач через tool_layer
        self._task_executor = None
        if tool_layer:
            try:
                from execution.task_executor import TaskExecutor
                import os as _os
                _working_dir = (
                    getattr(tool_layer, 'working_dir', None)
                    or (getattr(tool_layer.get('terminal'), 'working_dir', None)
                        if hasattr(tool_layer, 'get') else None)
                    or _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                )
                self._task_executor = TaskExecutor(
                    tool_layer=tool_layer,
                    working_dir=_working_dir,
                )
            except Exception as exc:
                import logging as _init_log
                _init_log.getLogger('telegram_bot').debug(
                    'TaskExecutor init skipped: %s', exc,
                )
        self.personality = personality or ""
        self._communication_style = communication_style
        self._chat_history: dict[str, list] = {}   # история диалога per chat_id (последние 20 шагов)
        self._chat_history_lock = threading.Lock()

        self._offset = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._poll_backoff = 1.0
        self._poll_backoff_max = 60.0
        self._session = requests.Session()
        # Таймауты гарантируются через self._request(), а не через атрибут сессии
        # Важно: не фиксируем глобальный Content-Type, иначе multipart upload
        # (sendVoice/sendAudio) ломается и Telegram не видит файл.

        # ── Ожидающие подтверждения человека ──────────────────────────────
        # {approval_id: {'event': threading.Event, 'result': bool}}
        self._pending_approvals: dict[str, dict] = {}
        self._approvals_lock = threading.Lock()

    def set_chat_history(self, actor_id: str, history: list):
        """Восстанавливает историю чата для заданного actor_id (потокобезопасно)."""
        with self._chat_history_lock:
            self._chat_history[actor_id] = history

    def __repr__(self) -> str:
        """SECURITY: маскируем токен в repr/traceback."""
        return f'<TelegramBot token=***masked*** chats={self.allowed_chat_ids}>'

    # ── Публичный интерфейс ───────────────────────────────────────────────────

    def start(self):
        """Запускает бота в фоновом потоке."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._log('Telegram Bot запущен')

    def stop(self):
        """Останавливает бота."""
        self._running = False
        self._log('Telegram Bot остановлен')

    def send(self, chat_id: int, text: str, parse_mode: str = 'HTML') -> bool:
        """Отправляет сообщение в чат. Длинные тексты разбиваются на части."""
        _MAX = 4096
        if len(text) <= _MAX:
            return self._api('sendMessage', {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode,
            })
        # Разбиваем по границам строк, чтобы не резать HTML-теги
        ok = True
        pos = 0
        part_num = 0
        while pos < len(text):
            chunk_end = pos + _MAX
            if chunk_end < len(text):
                # Ищем последний перенос строки в пределах лимита
                nl = text.rfind('\n', pos, chunk_end)
                if nl > pos:
                    chunk_end = nl + 1
            chunk = text[pos:chunk_end]
            part_num += 1
            if not self._api('sendMessage', {
                'chat_id': chat_id,
                'text': chunk,
                'parse_mode': parse_mode,
            }):
                ok = False
            pos = chunk_end
        return ok

    def _on_bridge_event(self, event):
        """Callback от ChannelBridge: событие из другого канала → уведомление в Telegram."""
        allowed = self.allowed_chat_ids
        if not isinstance(allowed, list) or not allowed:
            return
        chat_id: int = allowed[0]  # pylint: disable=unsubscriptable-object
        source_label = {'web': '🌐 Web', 'loop': '🔄 Цикл',
                        'system': '⚙️ Система'}.get(event.source, event.source)
        type_label = {'task_received': '📥 Задача',
                      'task_progress': '⏳',
                      'task_done': '✅ Готово',
                      'message': '💬',
                      'reply': '🤖'}.get(event.type, event.type)
        msg = f"{source_label} {type_label}: {event.text[:300]}"
        try:
            self.send(chat_id, self._escape(msg))
        except Exception as exc:
            self._log(f'bridge event send failed: {exc}', level='error')

    def broadcast(self, text: str):
        """Отправляет сообщение во все разрешённые чаты."""
        allowed_ids = self.allowed_chat_ids or []
        for chat_id in allowed_ids:
            self.send(chat_id, text)

    # ── Human Approval через Telegram ────────────────────────────────────────

    def request_approval(self, action_type: str, payload, timeout: int = 300) -> bool:
        """
        Отправляет запрос на подтверждение в Telegram (inline-кнопки Да/Нет).
        Блокирует до получения ответа или истечения таймаута (timeout сек → авто-отказ).
        Используется HumanApprovalLayer(mode='callback', callback=bot.request_approval).
        """
        allowed_ids = self.allowed_chat_ids or []
        if not allowed_ids:
            # Telegram не настроен — fail-closed: отказываем, чтобы не обходить human approval
            if self.monitoring:
                try:
                    self.monitoring.warning(
                        '[TelegramBot] request_approval отклонён: allowed_chat_ids пуст',
                        source='telegram_bot',
                    )
                except Exception:
                    pass
            return False

        approval_id = str(uuid.uuid4())[:8]
        event = threading.Event()
        with self._approvals_lock:
            self._pending_approvals[approval_id] = {
                'event': event,
                'result': False,
                'chat_ids': list(allowed_ids),
                'created_at': time.time(),
                'action_type': str(action_type),
            }

        # Формируем текст запроса
        payload_text = str(payload)[:800]
        text = (
            f"⚠️ <b>Требуется подтверждение</b>\n"
            f"Тип: <code>{self._escape(action_type)}</code>\n\n"
            f"{self._escape(payload_text)}\n\n"
            f"Если кнопки не сработают, ответь текстом: <code>да {approval_id}</code> "
            f"или <code>нет {approval_id}</code>."
        )
        keyboard = {
            'inline_keyboard': [[
                {'text': '✅ Да', 'callback_data': f'approval:{approval_id}:yes'},
                {'text': '❌ Нет', 'callback_data': f'approval:{approval_id}:no'},
            ]]
        }
        for chat_id in allowed_ids:
            self._api('sendMessage', {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'reply_markup': keyboard,
            })

        # Ждём ответа
        answered = event.wait(timeout=timeout)
        with self._approvals_lock:
            entry = self._pending_approvals.pop(approval_id, {})
        if not answered:
            for chat_id in allowed_ids:
                self.send(chat_id, f'⏱ Запрос <code>{approval_id}</code> истёк — действие отменено.')
            return False
        return entry.get('result', False)

    def _resolve_approval(self, approval_id: str, approved: bool):
        """Вызывается из обработчика callback_query — отмечает результат.
        Все операции под lock для предотвращения race condition."""
        with self._approvals_lock:
            entry = self._pending_approvals.get(approval_id)
            if not entry:
                return
            if entry.get('resolved'):
                return   # уже обработан (защита от двойного подтверждения)
            entry['result'] = approved
            entry['resolved'] = True
            event = entry.get('event')
        if event:
            event.set()

    # ── Long-polling цикл ─────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            try:
                updates = self._get_updates()
                self._poll_backoff = 1.0
                for update in updates:
                    self._handle_update(update)
            except Exception as e:  # pylint: disable=broad-except
                self._log(f'Ошибка polling: {e}', level='error')
                time.sleep(self._poll_backoff)
                self._poll_backoff = min(self._poll_backoff_max, self._poll_backoff * 2.0)

    def _get_updates(self) -> list[dict]:
        """Получает обновления из Telegram API.
        
        Контракт: ВСЕГДА возвращает list[dict], никогда не бросает.
        Ошибки логируются внутри.
        """
        try:
            resp = self._session.post(
                self._url('getUpdates'),
                json={'offset': self._offset, 'timeout': self.POLL_TIMEOUT},
                timeout=self.POLL_TIMEOUT + 5,
            )
            data = resp.json()
            if not data.get('ok'):
                self._log(
                    f"getUpdates: Telegram вернул ok=false: {data.get('description', '?')[:200]}",
                    level='error',
                )
                return []
            updates = data.get('result', [])
            if updates:
                self._offset = updates[-1]['update_id'] + 1
            return updates
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            self._log(f'getUpdates network/parse error: {exc}', level='error')
            return []

    # ── Обработка сообщений ───────────────────────────────────────────────────

    def _handle_update(self, update: dict):
        # ── Inline-кнопки (ответы на approval-запросы) ───────────────────────
        cq = update.get('callback_query')
        if cq:
            self._handle_callback_query(cq)
            return

        msg = update.get('message') or update.get('edited_message')
        if not msg:
            return

        chat_id = msg['chat']['id']
        text    = (msg.get('text') or '').strip()
        caption = (msg.get('caption') or '').strip()
        user    = msg.get('from', {})
        username = user.get('username') or user.get('first_name', 'user')
        actor_id = str(user.get('id', 'unknown'))

        # Проверка доступа
        allowed_ids = self.allowed_chat_ids or []
        if not allowed_ids or chat_id not in allowed_ids:
            self.send(chat_id, '⛔ Доступ запрещён.')
            return

        # Текстовый fallback для approval (если inline callback не пришёл)
        if text and self._try_resolve_approval_from_text(chat_id, text):
            return

        # ── Маршрутизация по типу контента ───────────────────────────────────

        # 🎤 Голос / аудио
        voice_obj = msg.get('voice') or msg.get('audio')
        if voice_obj:
            self._handle_voice(chat_id, voice_obj, actor_id, username)
            return

        # 🖼 Фото
        photos = msg.get('photo')
        if photos:
            self._handle_photo(chat_id, photos, caption, actor_id, username)
            return

        # 🎬 Видео / кружок / GIF
        video_obj = msg.get('video') or msg.get('video_note') or msg.get('animation')
        if video_obj:
            self._handle_video(chat_id, video_obj, caption, actor_id, username)
            return

        # 📄 Документ (PDF, DOCX, код, архивы и т.д.)
        document = msg.get('document')
        if document:
            self._handle_document(chat_id, document, caption, actor_id, username)
            return

        # 😀 Стикер
        sticker = msg.get('sticker')
        if sticker:
            self._handle_sticker(chat_id, sticker, actor_id)
            return

        # 📍 Геолокация
        location = msg.get('location')
        if location:
            self._handle_location(chat_id, location, actor_id)
            return

        # 📞 Контакт
        contact = msg.get('contact')
        if contact:
            self._handle_contact(chat_id, contact, actor_id)
            return

        # 💬 Текст
        if not text:
            return

        self._log(f'[{username}] {text}')
        if text.startswith('/'):
            self._handle_command(chat_id, text, actor_id, username)
        else:
            self._handle_chat(chat_id, text, actor_id)

    def _handle_command(self, chat_id: int, text: str, _actor_id: str, _username: str):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split('@')[0]   # убираем @botname
        arg = parts[1].strip() if len(parts) > 1 else ''

        if cmd == '/start':
            # Сначала ставим флаг, чтобы ProactiveMind.greet() не отправил дубль
            if self.proactive_mind:
                self.proactive_mind.mark_startup_done()
            self.send(chat_id, self._welcome_text())
            if self.proactive_mind:
                self.proactive_mind.on_user_message(text)

        elif cmd == '/help':
            self.send(chat_id, self._help_text())

        elif cmd == '/status':
            self.send(chat_id, self._status_text())

        elif cmd == '/budget':
            self.send(chat_id, self._budget_text())

        elif cmd == '/goal':
            if not arg:
                self.send(chat_id, '⚠️ Укажи цель: /goal <описание цели>')
                return
            reply = self._set_goal(arg)
            self.send(chat_id, reply)

        elif cmd == '/run':
            if not arg:
                self.send(chat_id, '⚠️ Укажи задачу: /run <задача>')
                return
            self.send(chat_id, f'🚀 Запускаю: <b>{arg[:100]}</b>')
            reply = self._run_goal(arg)
            self.send(chat_id, reply)

        elif cmd == '/stop':
            reply = self._stop_loop()
            self.send(chat_id, reply)

        elif cmd == '/search':
            if not arg:
                self.send(chat_id, '⚠️ Укажи запрос: /search <запрос>')
                return
            self.send(chat_id, f'🔍 Ищу: <i>{arg[:80]}</i>...')
            reply = self._search(arg)
            self.send(chat_id, reply)

        elif cmd == '/verify':
            if not arg:
                self.send(chat_id, '⚠️ Укажи утверждение: /verify <факт>')
                return
            self.send(chat_id, '🔎 Верифицирую...')
            reply = self._verify(arg)
            self.send(chat_id, reply)

        else:
            self.send(chat_id, f'❓ Неизвестная команда: {cmd}\n\n{self._help_text()}')

    def _handle_callback_query(self, cq: dict):
        """Обрабатывает нажатие inline-кнопки (Да / Нет для approval-запросов)."""
        cq_id   = cq.get('id', '')
        data    = cq.get('data', '')
        chat_id = (cq.get('message') or {}).get('chat', {}).get('id')

        # Отвечаем Telegram, чтобы убрать "часики" на кнопке
        self._api('answerCallbackQuery', {'callback_query_id': cq_id})

        if not data.startswith('approval:'):
            return

        parts = data.split(':')
        if len(parts) != 3:
            return

        _, approval_id, answer = parts
        approved = (answer == 'yes')
        self._resolve_approval(approval_id, approved)

        if chat_id:
            verdict = '✅ Одобрено' if approved else '❌ Отклонено'
            self.send(chat_id, f'{verdict} (запрос <code>{approval_id}</code>)',
                      parse_mode='HTML')

    def _try_resolve_approval_from_text(self, chat_id: int, text: str) -> bool:
        """
        Разрешает approval по тексту:
        - "да" / "нет"  -> последний ожидающий запрос для этого чата
        - "да <id>" / "нет <id>" -> конкретный approval_id
        """
        t = (text or '').strip().lower()
        yes_words = {'да', 'yes', '+', 'ок', 'ok', 'ага', 'подтверждаю'}
        no_words = {'нет', 'no', '-', 'неа', 'отмена', 'cancel'}

        parts = t.split()
        if not parts:
            return False

        decision = None
        if parts[0] in yes_words:
            decision = True
        elif parts[0] in no_words:
            decision = False
        else:
            return False

        requested_id = parts[1] if len(parts) > 1 else None

        with self._approvals_lock:
            items = list(self._pending_approvals.items())

        if not items:
            return False

        target_id = None
        target = None

        # 1) Явный ID: да <id> / нет <id>
        if requested_id:
            with self._approvals_lock:
                entry = self._pending_approvals.get(requested_id)
            if entry and (not entry.get('chat_ids') or chat_id in entry.get('chat_ids', [])):
                target_id = requested_id
                target = entry

        # 2) Без ID: берём самый свежий ожидающий для этого чата
        if target is None:
            for approval_id, entry in sorted(
                items,
                key=lambda kv: float(kv[1].get('created_at', 0)),
                reverse=True,
            ):
                chat_ids = entry.get('chat_ids') or []
                if (not chat_ids) or (chat_id in chat_ids):
                    target_id = approval_id
                    target = entry
                    break

        if not target_id or not target:
            return False

        self._resolve_approval(target_id, decision)
        verdict = '✅ Одобрено' if decision else '❌ Отклонено'
        self.send(chat_id, f'{verdict} (запрос <code>{target_id}</code>)', parse_mode='HTML')
        return True

    def _handle_chat(self, chat_id: int, text: str, actor_id: str):
        """Обычный чат — ответ через Cognitive Core с личностью и памятью."""
        if not self.cognitive_core:
            self.send(chat_id, '⚠️ Cognitive Core не подключён.')
            return
        # Ограничиваем длину входного текста — слишком длинный промпт
        # может уронить LLM-бэкенд или вызвать OOM
        _MAX_CHAT_TEXT = 4000
        if len(text) > _MAX_CHAT_TEXT:
            text = text[:_MAX_CHAT_TEXT] + '… [обрезано]'

        # Уведомляем ProactiveMind о сообщении пользователя
        if self.proactive_mind:
            self.proactive_mind.on_user_message(text)

        # Уведомляем другие каналы (Web) что пришло сообщение из Telegram
        if self.channel_bridge:
            _preview = text[:200].replace('\n', ' ')
            if not text.startswith('['):
                self.channel_bridge.task_received('telegram', _preview)

        # Документы / медиа (содержат тело файла или описание) — не пропускаем
        # через action-path, иначе ключевые слова из ТЕЛА дают ложное срабатывание.
        _ATTACHMENT_PREFIXES = (
            '[Пользователь уже приложил документ',
            '[Документ ',
            '[Файл:',
            '[Фото',
            '[Стикер',
            '[Геолокация',
            '[Контакт',
            '[Голос',
            '[Видео',
        )
        _is_attachment = text.startswith(_ATTACHMENT_PREFIXES)

        # Action-first: если пользователь просит сделать задачу, сначала пробуем выполнить.
        if not _is_attachment and self._is_actionable_request(text):
            exec_reply = self._execute_actionable_request(text, chat_id=chat_id)
            # None = ничего не сделано → продолжаем нормальный чат
            # ""   = файл уже отправлен (sendDocument/sendPhoto)
            # str  = текстовый ответ
            if exec_reply is not None:
                if exec_reply:
                    exec_reply = self._sanitize_user_response(exec_reply, user_text=text)
                    if self.persistent_brain:
                        self.persistent_brain.record_conversation(
                            role="user", message=text, response=exec_reply
                        )
                    self._learn_from_interaction(text, exec_reply)
                    self.send(chat_id, self._escape(exec_reply))
                return  # файл уже отправлен или есть текстовый ответ

        response = self._compose_chat_response(text, actor_id)

        # Записываем разговор в персистентную память
        if self.persistent_brain:
            self.persistent_brain.record_conversation(
                role="user", message=text, response=response
            )

        # Обучение: записываем эпизод в ExperienceReplay + LearningSystem
        self._learn_from_interaction(text, response)

        self.send(chat_id, self._escape(response))

        # Уведомляем другие каналы об ответе
        if self.channel_bridge:
            _rp = response[:200].replace('\n', ' ')
            self.channel_bridge.task_done('telegram', _rp)

    def _learn_from_interaction(self, user_text: str, response: str):
        """Записывает диалог как эпизод опыта для обучения агента."""
        # ExperienceReplay: записываем эпизод
        if self.experience_replay and user_text:
            try:
                self.experience_replay.add(
                    goal=user_text[:500],
                    actions=[{'type': 'telegram_chat', 'message': user_text[:300]}],
                    outcome=response[:500],
                    success=bool(response and not response.startswith('⚠️')),
                    context={'channel': 'telegram'},
                )
            except Exception as exc:
                self._log(f'experience_replay.add failed: {exc}', level='error')

        # LearningSystem: извлекаем знания из диалога
        if self.learning_system and user_text and response:
            try:
                content = f"Вопрос: {user_text}\nОтвет: {response}"
                self.learning_system.learn_from(
                    content=content[:2000],
                    source_type='conversation',
                    source_name='telegram_chat',
                    tags=['telegram', 'dialog'],
                )
            except Exception as exc:
                self._log(f'learning_system.learn_from failed: {exc}', level='error')

    def _build_chat_knowledge_context(self, text: str, max_items: int = 5) -> str:
        """Собирает компактный контекст релевантных знаний для чат-ответа."""
        if not self.knowledge or not text:
            return ""

        try:
            found = self.knowledge.get_relevant_knowledge(text)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return ""

        if not found:
            return ""

        lines: list[str] = []
        added = 0

        def _format_item(bucket_name: str, item) -> str:
            # Красивый формат для SearchResult из VectorStore
            try:
                if hasattr(item, 'doc') and hasattr(item, 'score'):
                    doc = getattr(item, 'doc', None)
                    doc_id = getattr(doc, 'doc_id', 'unknown') if doc else 'unknown'
                    text_preview = getattr(doc, 'text', '') if doc else ''
                    score = float(getattr(item, 'score', 0.0))
                    return (
                        f"- [{bucket_name}] doc_id={str(doc_id)[:80]}, "
                        f"score={score:.3f}, text={str(text_preview)[:120]}"
                    )

                if hasattr(item, 'to_dict'):
                    as_dict = item.to_dict()
                    if isinstance(as_dict, dict):
                        doc_id = as_dict.get('doc_id', 'unknown')
                        score = as_dict.get('score', 0.0)
                        preview = as_dict.get('text_preview', '')
                        return (
                            f"- [{bucket_name}] doc_id={str(doc_id)[:80]}, "
                            f"score={score}, text={str(preview)[:120]}"
                        )
            except (AttributeError, TypeError, ValueError):
                pass

            return f"- [{bucket_name}] {str(item)[:180]}"

        for bucket, payload in found.items():
            if added >= max_items:
                break

            if isinstance(payload, dict):
                for key, value in payload.items():
                    lines.append(f"- [{bucket}] {str(key)[:80]}: {str(value)[:140]}")
                    added += 1
                    if added >= max_items:
                        break
            elif isinstance(payload, list):
                for item in payload[:max_items - added]:
                    lines.append(_format_item(bucket, item))
                    added += 1
                    if added >= max_items:
                        break
            else:
                lines.append(f"- [{bucket}] {str(payload)[:180]}")
                added += 1

        return "\n".join(lines)

    def _compose_chat_response(self, text: str, actor_id: str) -> str:
        """Единая логика сборки контекста и генерации ответа для текста/голоса."""
        memory_ctx = ""
        if self.cognitive_core is None:
            return ""
        if self.persistent_brain:
            memory_ctx = self.persistent_brain.get_memory_context(text)

        social_ctx = ""
        social_style = None
        social_tone = 'friendly'
        if self.social_model:
            try:
                if hasattr(self.social_model, 'get_actor') and hasattr(self.social_model, 'register_actor'):
                    actor = self.social_model.get_actor(actor_id)
                    if not actor:
                        actor = self.social_model.register_actor(actor_id, f"user_{actor_id[-4:]}")

                    if hasattr(self.social_model, 'detect_style'):
                        self.social_model.detect_style(actor_id, text)
                    if hasattr(self.social_model, 'detect_tone'):
                        self.social_model.detect_tone(text, actor_id=actor_id)

                    if hasattr(actor, 'preferred_style') and hasattr(actor.preferred_style, 'value'):
                        social_style = actor.preferred_style.value

                if hasattr(self.social_model, 'suggest_response_tone'):
                    social_tone = self.social_model.suggest_response_tone(actor_id)

                if social_style or social_tone:
                    social_ctx = (
                        "Социальный контекст ответа:\n"
                        f"- Тон ответа: {social_tone}\n"
                        f"- Стиль ответа: {social_style or 'friendly'}"
                    )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

        knowledge_ctx = self._build_chat_knowledge_context(text)

        system = self.personality or ""
        if memory_ctx:
            system += f"\n\nТвоя память:\n{memory_ctx}"
        if knowledge_ctx:
            system += f"\n\nРелевантные выученные знания:\n{knowledge_ctx}"
        if social_ctx:
            system += f"\n\n{social_ctx}"

        # Эмоциональное состояние агента (собственное настроение)
        if self.social_model and hasattr(self.social_model, 'emotional_state'):
            try:
                mood_directive = self.social_model.emotional_state.get_mood_directive()
                if mood_directive:
                    system += f"\n\nТвоё текущее эмоциональное состояние:\n{mood_directive}"
            except (AttributeError, TypeError):
                pass

        # Если это small-talk — явно подсказываем LLM не превращать в задачу
        _is_small = self._is_small_talk(text)
        if _is_small:
            system += (
                "\n\nВАЖНО: Это неформальное обращение или приветствие — обычный живой разговор. "
                "Отвечай кратко и по-человечески, как в реальной беседе. "
                "Не превращай это в задачу, не составляй план, не используй пункты и нумерацию."
            )
        else:
            system += (
                "\n\nВАЖНО: Это Telegram — общайся как живой человек, просто и понятно. "
                "Никогда не выводи технические/служебные данные: метрики, success rate, score, "
                "confidence, циклы, reasoning, debug, system, vector search, traceback, стек вызовов, "
                "'текст обрезан', имена классов, JSON, внутренние ID. "
                "Если что-то не получилось — скажи простым языком что не так и что делать, "
                "без технических деталей. Пиши коротко, дружелюбно, по делу."
            )

        # Собираем историю диалога для этого chat_id (последние 20 обменов)
        with self._chat_history_lock:
            chat_history = self._chat_history.get(actor_id, [])
            history_for_llm = chat_history[-20:] if chat_history else []

        try:
            response = str(self.cognitive_core.converse(text, system=system, history=history_for_llm))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            # LLM bypass fallback — логируем для аудита, не используем память/историю
            try:
                import logging as _lb_log
                _lb_log.getLogger('telegram_bot').warning(
                    'LLM bypass: converse() failed, falling back to llm.infer()'
                )
            except Exception:
                pass
            response = str(self.cognitive_core.llm.infer(text, system=system))

        # Сохраняем этот обмен в историю
        with self._chat_history_lock:
            chat_history = self._chat_history.get(actor_id, [])
            chat_history.append({'role': 'user', 'content': text})
            chat_history.append({'role': 'assistant', 'content': response})
            self._chat_history[actor_id] = chat_history[-40:]  # храним последние 20 обменов (40 сообщений)

        # adapt_response переформулирует ответ — для small-talk это ломает естественный тон
        if self.social_model and not _is_small:
            try:
                response = self.social_model.adapt_response(response, actor_id)
            except TypeError:
                try:
                    response = self.social_model.adapt_response(
                        response,
                        tone=social_tone,
                        style=social_style or 'friendly',
                        context={'high_priority': 'urgent' in (social_tone or '')},
                    )
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass
            except (AttributeError, ValueError, RuntimeError):
                pass

        return self._sanitize_user_response(response, user_text=text)

    @staticmethod
    def _looks_internal_telemetry(text: str) -> bool:
        """Определяет, что в тексте протекли внутренние служебные маркеры."""
        return looks_internal_telemetry(text)

    def _sanitize_user_response(self, text: str, user_text: str = "") -> str:
        """Очищает финальный ответ от логов/метрик и возвращает человеко-понятный текст."""
        return sanitize_user_response(text, user_text=user_text, style=self._communication_style)

    @staticmethod
    def _is_small_talk(text: str) -> bool:
        """Определяет: это просто разговор/приветствие, а не задача."""
        t = (text or '').strip().lower()
        if not t:
            return False
        _SMALL_TALK = (
            'привет', 'приветствую', 'здравствуй', 'здрасте', 'здорово',
            'хай', 'хей', 'хэй', 'hi', 'hello', 'hey', 'хаюхай',
            'пока', 'до свидания', 'до встречи', 'увидимся', 'пока-пока',
            'bye', 'goodbye', 'cya',
            'как дела', 'как ты', 'как у тебя', 'как жизнь', 'как настроение',
            'как сам', 'что нового', 'что делаешь', 'чем занимаешься',
            'ты тут', 'ты здесь', 'живой', 'окей', 'ок', 'ok',
            'доброе утро', 'добрый день', 'добрый вечер', 'спокойной ночи',
            'спасибо', 'благодарю', 'спс', 'thanks', 'thank you',
            'понял', 'понятно', 'ясно', 'хорошо', 'ладно', 'согласен',
            'будь человеком', 'отвечай по-человечески', 'говори как человек',
            'скажи по-человечески', 'ты же человек', 'живой', 'ты живой',
        )
        # Совпадение всего сообщения или начало с маркера small-talk
        for marker in _SMALL_TALK:
            if t == marker or t.startswith(marker + ' ') or t.startswith(marker + ','):
                return True
        # Очень короткое (≤3 слов) без глаголов действия
        words = t.split()
        _ACTION_VERBS = ('сделай', 'выполни', 'запусти', 'проверь', 'исправь',
                         'найди', 'создай', 'удали', 'добавь', 'run', 'fix',
                         'check', 'analyze', 'implement', 'изучи', 'прочитай')
        if len(words) <= 3 and not any(v in t for v in _ACTION_VERBS):
            return True
        return False

    def _is_actionable_request(self, text: str) -> bool:
        """Эвристика: сообщение похоже на задачу, которую нужно делать, а не обсуждать."""
        text_l = (text or '').lower()
        if not text_l.strip():
            return False

        # Явный small-talk / приветствие — никогда не задача
        if self._is_small_talk(text):
            return False

        action_keywords = [
            # Повелительное наклонение
            'сделай', 'выполни', 'запусти', 'проверь', 'исправь', 'почини',
            'проанализируй', 'найди', 'собери', 'создай', 'обнови', 'добавь',
            'удали', 'прогони', 'подключи', 'отключи', 'открой', 'закрой',
            'скачай', 'установи', 'настрой', 'переименуй', 'перенеси', 'скопируй',
            'посмотри', 'загрузи', 'сохрани', 'очисти', 'перезапусти', 'останови',
            'run ', 'fix ', 'check ', 'analyze ', 'implement ', 'find ', 'search ',
            # Инфинитивы и «попробуй ...»
            'найти', 'подключить', 'исправить', 'починить', 'запустить',
            'проверить', 'сделать', 'выполнить', 'создать', 'добавить',
            'удалить', 'обновить', 'посмотреть', 'разобраться', 'настроить',
            'скачать', 'установить', 'очистить', 'перезапустить', 'остановить',
            'попробовать', 'попробуй',
            # Учёба и исследование
            'изучи', 'начни изучать', 'начать изучать', 'изучить', 'исследуй',
            'изучай', 'обучись', 'освой', 'разберись', 'читай', 'прочитай',
            'выучи', 'запомни', 'узнай подробно', 'изучи тему',
        ]
        # Чисто информационные / вопросительные фразы — не задачи
        question_only_markers = [
            'что это', 'почему', 'объясни', 'расскажи', 'как работает',
            'что происходит', 'что случилось', 'что нового', 'что такое',
            'скажи', 'расскажи мне',
            'какие новости', 'мировые новости', 'последние новости',
        ]

        if any(marker in text_l for marker in question_only_markers):
            # Считаем actionable только если есть явный глагол действия
            return any(k in text_l for k in action_keywords if k not in
                       ('найди', 'собери', 'посмотри'))  # в вопросительном контексте = info

        return any(k in text_l for k in action_keywords)

    def _execute_actionable_request(self, text: str, chat_id: int | None = None) -> str | None:
        """
        Пытается выполнить задачу.

        Returns:
            None  — ничего не сделано, продолжить нормальный чат
            ""    — задача выполнена (файл уже отправлен), остановиться
            str   — текстовый ответ для отправки пользователю
        """
        if not self.loop:
            # Loop не подключён — пробуем TaskExecutor напрямую
            if self._task_executor:
                try:
                    tex = self._task_executor.execute(text)
                    reply = self._format_task_result(tex, chat_id)
                    # _format_task_result returns "" when file was sent, or text/None
                    return reply if reply is not None else None
                except Exception:
                    pass
            if self.cognitive_core:
                try:
                    r = str(self.cognitive_core.process_task(text))
                    return r if r.strip() else None
                except AttributeError:
                    pass
            return None

        try:
            self.loop.set_goal(text)
            cycle = self.loop.step()
            result = cycle.to_dict() if hasattr(cycle, 'to_dict') else str(cycle)

            if isinstance(result, dict):
                # Показываем реальный результат действия, а не план
                action_result = result.get('action_result') or result.get('act_result')

                # Проверяем, создал ли TaskExecutor файл
                exec_data = (
                    action_result if isinstance(action_result, dict)
                    else result.get('execution', {})
                )
                if isinstance(exec_data, dict) and exec_data.get('file'):
                    reply = self._format_task_result(exec_data, chat_id)
                    if reply is not None:
                        return reply  # "" = file sent, or text

                if action_result and str(action_result).strip():
                    return self._escape(str(action_result)[:1200])

                # Если действия не было, но есть вывод — показываем его честно
                parts = []
                if result.get('plan'):
                    parts.append(f"План:\n{self._escape(str(result['plan'])[:400])}")
                if result.get('evaluation'):
                    parts.append(f"Оценка: {self._escape(str(result['evaluation'])[:200])}")
                if result.get('errors'):
                    parts.append(
                        "Ошибки: " + ", ".join(str(e) for e in result['errors'][:3])
                    )
                return "\n".join(parts) if parts else None

            text_result = str(result).strip()
            return self._escape(text_result[:1200]) if text_result else None
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            self._log(f'action execution failed: {e}', level='error')
            return None

    def _format_task_result(self, tex: dict, chat_id: int | None) -> str:
        """
        Форматирует результат TaskExecutor.
        Если создан файл и есть chat_id — отправляет файл через sendDocument/sendPhoto
        и возвращает пустую строку (файл уже отправлен).
        Иначе возвращает текстовый ответ.
        """
        import os
        file_path = tex.get('file', '')
        success = tex.get('success', False)
        task_type = tex.get('task_type', '')

        if file_path and os.path.isfile(file_path) and chat_id:
            caption = f"✅ Готово ({task_type}): {os.path.basename(file_path)}"
            if not success:
                caption = f"⚠️ Частично ({task_type}): {os.path.basename(file_path)}"
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.gif'):
                self._send_photo(chat_id, file_path, caption)
            else:
                self._send_document(chat_id, file_path, caption)
            return ""  # файл уже отправлен

        # Нет файла — текстовый ответ
        if success:
            data = tex.get('data')
            if data and isinstance(data, dict):
                lines = [f"<b>{k}</b>: {v}" for k, v in data.items()]
                return "\n".join(lines)
            return tex.get('note', '') or f"✅ Задача выполнена ({task_type})"
        return self._escape(tex.get('error', f'Ошибка: задача {task_type} не выполнена'))

    def _send_document(self, chat_id: int, file_path: str, caption: str = '') -> bool:
        """Отправляет файл как документ в Telegram."""
        import os
        try:
            url = self.BASE.format(token=self.token, method='sendDocument')
            with open(file_path, 'rb') as fh:
                resp = self._session.post(
                    url,
                    data={'chat_id': chat_id, 'caption': caption[:1024]},
                    files={'document': (os.path.basename(file_path), fh)},
                    timeout=60,
                )
            ok = resp.status_code == 200 and resp.json().get('ok', False)
            if not ok:
                self._log(f'sendDocument failed: {resp.text[:200]}', level='error')
            return ok
        except Exception as e:
            self._log(f'sendDocument error: {e}', level='error')
            return False

    def _send_photo(self, chat_id: int, file_path: str, caption: str = '') -> bool:
        """Отправляет изображение в Telegram."""
        import os
        try:
            url = self.BASE.format(token=self.token, method='sendPhoto')
            with open(file_path, 'rb') as fh:
                resp = self._session.post(
                    url,
                    data={'chat_id': chat_id, 'caption': caption[:1024]},
                    files={'photo': (os.path.basename(file_path), fh)},
                    timeout=60,
                )
            ok = resp.status_code == 200 and resp.json().get('ok', False)
            if not ok:
                self._log(f'sendPhoto failed: {resp.text[:200]}', level='error')
            return ok
        except Exception as e:
            self._log(f'sendPhoto error: {e}', level='error')
            return False

    # ── Текстовые шаблоны ─────────────────────────────────────────────────────

    def _welcome_text(self) -> str:
        # Если есть LLM и личность — генерируем живое приветствие
        # Используем llm.infer напрямую: converse() подмешивает внутренний контекст
        # (state, intent, context), который LLM потом пересказывает пользователю.
        if self.cognitive_core and self.personality:
            try:
                _system = (
                    self.personality + "\n\n"
                    "ВАЖНО: Это Telegram. Отвечай как живой человек, коротко. "
                    "Никаких технических данных, метрик, JSON, context, state, intent, "
                    "conversation_history, debug. Только человеческий текст."
                )
                raw = str(self.cognitive_core.llm.infer(
                    "Пользователь нажал /start. Кратко представься и предложи помощь. "
                    "2-3 предложения. Упомяни что можешь просто общаться или помогать с задачами.",
                    system=_system,
                ))
                return self._sanitize_user_response(raw, user_text="/start")
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        return (
            'Привет! Я автономный AI-агент.\n'
            'Просто напиши мне — поговорим, или дай задачу.\n\n'
            + self._help_text()
        )

    def _help_text(self) -> str:
        return (
            '<b>Команды:</b>\n'
            '/goal &lt;цель&gt; — поставить цель\n'
            '/run &lt;задача&gt; — выполнить задачу\n'
            '/stop — остановить цикл\n'
            '/search &lt;запрос&gt; — поиск в интернете\n'
            '/verify &lt;факт&gt; — проверить утверждение\n'
            '/status — состояние агента\n'
            '/budget — расход токенов и денег\n'
            '/help — эта справка\n\n'
            '🎤 <i>Голосовые сообщения поддерживаются (Whisper)</i>'
        )

    def _status_text(self) -> str:
        lines = ['<b>Состояние агента</b>']

        if self.monitoring:
            try:
                logs = self.monitoring.summary() if hasattr(self.monitoring, 'summary') else {}
                lines.append(f"📋 Логов: {logs.get('total_logs', '?')}, ошибок: {logs.get('errors', '?')}")
                smoke = logs.get('core_smoke', {}) if isinstance(logs, dict) else {}
                if smoke:
                    last_event = smoke.get('last_event') or {}
                    last_message = last_event.get('message', 'нет данных') if isinstance(last_event, dict) else 'нет данных'
                    lines.append(
                        f"🧪 Core smoke: OK={smoke.get('passed', 0)} | FAIL={smoke.get('failed', 0)} | Последнее: {last_message}"
                    )
            except (AttributeError, TypeError, ValueError, OSError):
                pass

        if self.hardware:
            try:
                m = self.hardware.collect()
                lines.append(
                    f"💻 CPU: {m.cpu_percent:.1f}% | "
                    f"RAM: {m.memory_percent:.1f}% ({m.memory_used_mb:.0f}/{m.memory_total_mb:.0f} МБ)"
                )
                if m.disk_percent is not None:
                    lines.append(
                        f"💾 Диск: {m.disk_percent:.1f}% занят "
                        f"({m.disk_free_gb:.1f} ГБ свободно)"
                    )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

        if self.loop:
            try:
                running = getattr(self.loop, '_running', False)
                cycles = getattr(self.loop, '_cycle_count', 0)
                lines.append(f"🔄 Цикл: {'активен' if running else 'остановлен'} | Циклов: {cycles}")
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

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
                lines.append(f"🎯 Активных целей: {len(active)}")
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

        if self.persistent_brain:
            try:
                compact = self.persistent_brain.compact_status_text(
                    max_solver_types=2,
                    max_challengers_per_solver=1,
                    max_chars=240,
                )
                if compact:
                    lines.append("🧠 Persistent Memory:")
                    lines.extend(compact.splitlines())
            except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
                pass

        return '\n'.join(lines) if len(lines) > 1 else '⚠️ Данные недоступны'

    def _budget_text(self) -> str:
        if not self.budget:
            return '⚠️ Budget Control не подключён'
        try:
            s = self.budget.summary()
            spent = s.get('spent', {})
            limits = s.get('limits', {})
            return (
                f"<b>Бюджет</b>\n"
                f"💰 Деньги: ${spent.get('money', 0):.4f} / ${limits.get('money', '?')}\n"
                f"🔤 Токены: {int(spent.get('tokens', 0)):,} / {int(limits.get('tokens', 0) or 0):,}\n"
                f"📡 Запросы: {int(spent.get('requests', 0))} / {int(limits.get('requests', 0) or 0)}\n"
                f"Статус: {s.get('overall_status', '?')}"
            )
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'⚠️ Ошибка: {e}'

    def _set_goal(self, description: str) -> str:
        if not self.goal_manager:
            return '⚠️ Goal Manager не подключён'
        try:
            goal = self.goal_manager.add(description)
            return f"✅ Цель добавлена:\n<b>{goal.description[:100]}</b>\nID: <code>{goal.goal_id}</code>"
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'❌ Ошибка: {e}'

    def _run_goal(self, goal: str) -> str:
        if not self.loop:
            return '⚠️ Autonomous Loop не подключён'
        try:
            self.loop.set_goal(goal)
            cycle = self.loop.step()
            result = cycle.to_dict() if hasattr(cycle, 'to_dict') else str(cycle)
            # Показываем ключевые результаты
            if isinstance(result, dict):
                parts = [f"✅ <b>Цикл #{result.get('cycle_id', '?')}</b>"]
                if result.get('analysis'):
                    parts.append(f"\n📊 {self._escape(str(result['analysis'])[:400])}")
                if result.get('plan'):
                    parts.append(f"\n📋 {self._escape(str(result['plan'])[:400])}")
                if result.get('evaluation'):
                    parts.append(f"\n💡 {self._escape(str(result['evaluation'])[:400])}")
                if result.get('errors'):
                    parts.append(f"\n⚠️ Ошибки: {', '.join(str(e) for e in result['errors'][:3])}")
                return ''.join(parts)
            return f"✅ Выполнено:\n{self._escape(str(result)[:800])}"
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'❌ Ошибка выполнения: {e}'

    def _stop_loop(self) -> str:
        if not self.loop:
            return '⚠️ Autonomous Loop не подключён'
        try:
            self.loop.stop()
            return '⏹ Автономный цикл остановлен.'
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'❌ Ошибка: {e}'

    def _search(self, query: str) -> str:
        if not self.search_tool:
            return '⚠️ Search не подключён'
        try:
            results = self.search_tool.run(query, num_results=5)
            items = results.get('results', [])
            if not items:
                return '🔍 Ничего не найдено'
            lines = [f'<b>Результаты для: {self._escape(query)}</b>']
            for r in items[:5]:
                title = self._escape(r.get('title', 'Без заголовка')[:80])
                url = r.get('url', '')
                snippet = self._escape(r.get('snippet', '')[:120])
                lines.append(f'• <a href="{url}">{title}</a>\n  {snippet}')
            return '\n\n'.join(lines)
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'❌ Ошибка поиска: {e}'

    def _verify(self, claim: str) -> str:
        if not self.verifier:
            return '⚠️ Knowledge Verifier не подключён'
        try:
            result = self.verifier.verify(claim)
            status = result.status.value if hasattr(result.status, 'value') else str(result.status)
            conf = result.confidence
            notes = result.notes or ''
            emoji = {
                'verified': '✅', 'unverified': '❓',
                'uncertain': '🤔', 'contested': '⚠️',
                'outdated': '🕐', 'false': '❌',
            }.get(status, '❓')
            return (
                f"{emoji} <b>{status.upper()}</b> ({conf:.0%})\n"
                f"Утверждение: <i>{self._escape(claim[:100])}</i>\n"
                + (f"Примечание: {self._escape(notes[:200])}" if notes else '')
            )
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            return f'❌ Ошибка: {e}'

    # ── Фото / Видео / Документы / Стикеры ───────────────────────────────────

    def _handle_photo(self, chat_id: int, photos: list, caption: str,
                      actor_id: str, username: str):
        """
        Фото → скачиваем лучшее качество → GPT-4o Vision → описание → LLM ответ.
        """
        best = photos[-1]   # последний = самое высокое разрешение
        path = self._download_file(best['file_id'], suffix='.jpg')
        if not path:
            self.send(chat_id, '❌ Не удалось скачать изображение.')
            return

        import os
        try:
            if self.image_recognizer:
                description = self.image_recognizer.describe(path)
            else:
                description = '[изображение получено, vision не подключён]'

            user_text = f'[Фото]: {description}'
            if caption:
                user_text = f'[Фото с подписью "{caption}"]: {description}'

            self._log(f'[{username}] Photo -> recognized ({len(description)} chars)')
            self._handle_chat(chat_id, user_text, actor_id)
        finally:
            try:
                os.unlink(path)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass

    def _handle_video(self, chat_id: int, video_obj: dict, caption: str,
                      actor_id: str, username: str):
        """
        Видео / кружок / GIF → анализируем миниатюру через Vision → ответ.
        """
        import os
        duration = video_obj.get('duration', 0)
        label = 'Видео' if 'duration' in video_obj else 'GIF'

        # Пробуем получить миниатюру для анализа
        thumb = video_obj.get('thumbnail') or video_obj.get('thumb')
        if thumb and self.image_recognizer:
            path = self._download_file(thumb['file_id'], suffix='.jpg')
            if path:
                try:
                    description = self.image_recognizer.describe(path)
                    user_text = f'[{label} {duration}с]: {description}'
                    if caption:
                        user_text = f'[{label} {duration}с, подпись: "{caption}"]: {description}'
                    self._log(f'[{username}] {label} thumbnail -> recognized ({len(description)} chars)')
                    self._handle_chat(chat_id, user_text, actor_id)
                    return
                finally:
                    try:
                        os.unlink(path)
                    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
                        pass

        # Fallback — нет миниатюры
        user_text = f'[{label} {duration}с]'
        if caption:
            user_text += f': {caption}'
        self._handle_chat(chat_id, user_text, actor_id)

    def _handle_document(self, chat_id: int, doc_obj: dict, caption: str,
                         actor_id: str, username: str):
        """
        Документ (PDF/DOCX/код/архив и т.д.) → скачиваем → DocumentParser → LLM.
        """
        import os
        file_id   = doc_obj['file_id']
        file_name = doc_obj.get('file_name', 'document')
        mime_type = doc_obj.get('mime_type', '')
        ext       = os.path.splitext(file_name)[1] or '.bin'

        path = self._download_file(file_id, suffix=ext)
        if not path:
            self.send(chat_id, '❌ Не удалось скачать документ.')
            return

        try:
            if self.document_parser:
                parsed = self.document_parser.parse(path)
                if parsed and parsed.text and parsed.text.strip():
                    preview = parsed.text[:3000]
                    user_text = self._format_document_chat_input(
                        file_name=file_name,
                        preview=preview,
                        caption=caption,
                        pages=parsed.pages,
                    )
                    self._log(f'[{username}] Document "{file_name}" -> {len(parsed.text)} chars')
                    self._handle_chat(chat_id, user_text, actor_id)
                    return

            # Fallback — не смогли прочитать (архив, бинарный и т.д.)
            user_text = f'[Документ "{file_name}" ({mime_type})]'
            if caption:
                user_text += f': {caption}'
            self._handle_chat(chat_id, user_text, actor_id)
        finally:
            try:
                os.unlink(path)
            except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
                pass

    @classmethod
    def _format_document_chat_input(cls, file_name: str, preview: str,
                                    caption: str = '', pages=None) -> str:
        """Формирует явный текст-вход для LLM по уже приложенному документу.

        Если caption содержит ключевые слова запроса мнения — агент анализирует.
        Иначе (пустой caption / команда действия) — агент исполняет как задание.
        """
        _intent = cls._detect_file_intent(caption)

        if _intent == 'execute':
            header = (
                f'Пользователь передал документ "{file_name}" '
                f'({pages or "?"} стр.) как РАБОЧЕЕ ЗАДАНИЕ. '
                'Прими его как спецификацию/ТЗ и НЕМЕДЛЕННО приступай к выполнению. '
                'НЕ пересказывай содержимое, НЕ анализируй структуру — ДЕЛАЙ то, '
                'что описано в документе. Если несколько задач — начни с первой.'
            )
        else:
            header = (
                f'Пользователь уже приложил документ "{file_name}" '
                f'({pages or "?"} стр.). Не проси прислать файл заново. '
                'Проанализируй содержимое и дай свою оценку.'
            )
        if caption:
            header += f' Подпись пользователя: "{caption}".'
        return f'[{header}]\n\nСОДЕРЖИМОЕ ДОКУМЕНТА:\n{preview}'

    # Ключевые слова запроса анализа/мнения (а не исполнения)
    _ANALYZE_KW = (
        'проанализируй', 'анализ', 'что думаешь', 'твоё мнение', 'твое мнение',
        'оцени', 'оценка', 'расскажи', 'объясни', 'вкратце', 'кратко',
        'резюме', 'суммируй', 'суммарно', 'обзор', 'ревью', 'review',
        'прочитай', 'покажи', 'опиши', 'перескажи', 'что здесь', 'что там',
        'что в файле', 'что в документе', 'разбери', 'разбор', 'проверь',
    )

    @classmethod
    def _detect_file_intent(cls, caption: str) -> str:
        """Определяет намерение: 'analyze' (мнение) или 'execute' (задание).
        
        По умолчанию — analyze (безопаснее). Явное execute только при
        наличии прямых команд действия в подписи.
        """
        msg = (caption or '').strip().lower()
        if not msg:
            return 'analyze'
        if any(kw in msg for kw in cls._ANALYZE_KW):
            return 'analyze'
        # Только при явных командах действия переключаемся в execute
        _EXECUTE_KW = (
            'выполни', 'сделай', 'реализуй', 'запусти', 'примени',
            'имплементируй', 'implement', 'execute', 'run', 'do it',
        )
        if any(kw in msg for kw in _EXECUTE_KW):
            return 'execute'
        return 'analyze'

    def _handle_sticker(self, chat_id: int, sticker: dict, actor_id: str):
        """Стикер → отвечаем в стиле агента."""
        emoji = sticker.get('emoji', '😄')
        user_text = f'[Стикер {emoji}]: пользователь отправил стикер.'
        self._handle_chat(chat_id, user_text, actor_id)

    def _handle_location(self, chat_id: int, location: dict, actor_id: str):
        """Геолокация → передаём координаты агенту."""
        lat = location.get('latitude', 0)
        lon = location.get('longitude', 0)
        user_text = f'[Геолокация]: широта {lat}, долгота {lon}.'
        self._handle_chat(chat_id, user_text, actor_id)

    def _handle_contact(self, chat_id: int, contact: dict, actor_id: str):
        """Контакт → передаём данные агенту."""
        name  = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
        phone = str(contact.get('phone_number', '') or '')
        digits = ''.join(ch for ch in phone if ch.isdigit())
        if digits:
            masked = '*' * max(0, len(digits) - 2) + digits[-2:]
        else:
            masked = 'скрыт'
        user_text = f'[Контакт]: {name}, телефон: {masked}.'
        self._handle_chat(chat_id, user_text, actor_id)

    # ── Голос ─────────────────────────────────────────────────────────────────

    def _handle_voice(self, chat_id: int, voice_obj: dict,
                      actor_id: str, username: str):
        """
        Обрабатывает голосовое / аудио сообщение:
          1. Скачивает OGG-файл с серверов Telegram
          2. Транскрибирует через Whisper (SpeechRecognizer)
          3. Отправляет подтверждение распознавания
          4. Обрабатывает текст как обычное сообщение (команда или чат)
        """
        if not self.speech_recognizer:
            self.send(chat_id, '⚠️ Распознавание речи не подключено.')
            return

        file_id = voice_obj.get('file_id')
        if not file_id:
            return

        self._log(f'[{username}] Voice message, file_id={file_id[:20]}...')

        # 1. Скачиваем OGG во временный файл
        audio_path = self._download_file(file_id)
        if not audio_path:
            self.send(chat_id, '❌ Не удалось скачать голосовое сообщение.')
            return

        # 2. Транскрибируем через Whisper
        import os
        try:
            result = self.speech_recognizer.transcribe(audio_path)
            text = result.text.strip()
        except (AttributeError, RuntimeError, ValueError, OSError) as e:
            self._log(f'Ошибка транскрипции: {e}', level='error')
            self.send(chat_id, f'❌ Ошибка распознавания: {e}')
            return
        finally:
            try:
                os.unlink(audio_path)
            except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError):
                pass

        if not text:
            self.send(chat_id, '⚠️ Не удалось распознать речь.')
            return

        self._log(f'[{username}] Voice -> "{text[:30]}…" ({len(text)} chars)')

        # 3. Обрабатываем как обычный текст, перехватываем ответ
        if text.startswith('/'):
            # Команды — только текст (статус, бюджет и т.д. не озвучиваем)
            self._handle_command(chat_id, text, actor_id, username)
        else:
            # Чат — генерируем ответ и отправляем голосом
            self._handle_chat_voice(chat_id, text, actor_id)

    def _handle_chat_voice(self, chat_id: int, text: str, actor_id: str):
        """
        Вариант _handle_chat для голосового режима:
        генерирует ответ через LLM, затем отправляет его как голосовое сообщение.
        Если TTS недоступен — fallback на текст.
        """
        if not self.cognitive_core:
            self.send(chat_id, '⚠️ Cognitive Core не подключён.')
            return

        if self.proactive_mind:
            self.proactive_mind.on_user_message(text)

        if self._is_actionable_request(text):
            exec_reply = self._execute_actionable_request(text, chat_id=chat_id)
            if exec_reply is not None and exec_reply:
                exec_reply = self._sanitize_user_response(exec_reply, user_text=text)
                if self.persistent_brain:
                    self.persistent_brain.record_conversation(
                        role="user", message=text, response=exec_reply
                    )
                if self.speech_synthesizer:
                    audio_path = self.speech_synthesizer.synthesize(exec_reply)
                    if audio_path:
                        ok = self.send_voice(chat_id, audio_path)
                        self.speech_synthesizer.cleanup(audio_path)
                        if ok:
                            return
                self.send(chat_id, self._escape(exec_reply))
            return

        response = self._compose_chat_response(text, actor_id)

        if self.persistent_brain:
            self.persistent_brain.record_conversation(
                role="user", message=text, response=response
            )

        # Синтезируем ответ в голос
        if self.speech_synthesizer:
            audio_path = self.speech_synthesizer.synthesize(response)
            if audio_path:
                ok = self.send_voice(chat_id, audio_path)
                self.speech_synthesizer.cleanup(audio_path)
                if ok:
                    return  # успешно отправили голосом
        # Fallback: текст
        self.send(chat_id, self._escape(response))

    def send_voice(self, chat_id: int, audio_path: str) -> bool:
        """
        Отправляет аудио ответ в Telegram.

        1) Сначала пытается отправить как voice note (sendVoice).
        2) Если Telegram отклоняет voice (часто из-за контейнера/кодека),
           делает fallback на sendAudio, чтобы пользователь всё равно
           получил озвученный ответ.
        """
        try:
            # Попытка 1: voice note
            voice_url = self._url('sendVoice')
            with open(audio_path, 'rb') as f:
                resp = self._session.post(
                    voice_url,
                    data={'chat_id': chat_id},
                    files={'voice': ('voice.opus', f, 'audio/opus')},
                    timeout=30,
                )
            result = resp.json()

            if result.get('ok', False):
                return True

            self._log(f'sendVoice error: {result}', level='error')

            # Попытка 2: fallback на sendAudio
            audio_url = self._url('sendAudio')
            with open(audio_path, 'rb') as f:
                resp2 = self._session.post(
                    audio_url,
                    data={
                        'chat_id': chat_id,
                        'title': 'Agent voice response',
                    },
                    files={'audio': ('response.opus', f, 'audio/opus')},
                    timeout=30,
                )
            result2 = resp2.json()
            if not result2.get('ok', False):
                self._log(f'sendAudio fallback error: {result2}', level='error')
            else:
                self._log('sendVoice rejected, delivered via sendAudio fallback')
            return result2.get('ok', False)
        except (requests.RequestException, ValueError, OSError) as e:
            self._log(f'send_voice error: {e}', level='error')
            return False

    def _download_file(self, file_id: str, suffix: str | None = None) -> str | None:
        """
        Универсальный загрузчик файлов из Telegram.
        Запрашивает file_path через getFile, скачивает, сохраняет во tempfile.

        Args:
            file_id — Telegram file_id
            suffix  — расширение (.ogg, .jpg, .pdf ...). Если None — из file_path.

        Returns:
            Путь к временному файлу или None при ошибке.
        """
        import tempfile
        import os
        try:
            resp = self._session.post(
                self._url('getFile'),
                json={'file_id': file_id},
                timeout=10,
            )
            data = resp.json()
            if not data.get('ok'):
                self._log(f'getFile error: {data}', level='error')
                return None

            file_path = data['result']['file_path']

            download_url = (
                f'https://api.telegram.org/file/bot{self.token}/{file_path}'
            )
            r = self._session.get(download_url, timeout=60)
            r.raise_for_status()

            ext = suffix or os.path.splitext(file_path)[1] or '.bin'
            fd, tmp_path = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, 'wb') as f:
                f.write(r.content)

            self._log(f'Downloaded: {len(r.content)} bytes -> {tmp_path}')
            return tmp_path

        except (requests.RequestException, ValueError, KeyError, OSError) as e:
            self._log(f'Ошибка скачивания файла: {e}', level='error')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _url(self, method: str) -> str:
        return self.BASE.format(token=self.token, method=method)

    def _api(self, method: str, payload: dict) -> bool:
        try:
            resp = self._session.post(self._url(method), json=payload, timeout=10)
            return resp.json().get('ok', False)
        except (requests.RequestException, ValueError, TypeError) as e:
            # SECURITY: маскируем токен в сообщениях исключений requests
            err_msg = str(e).replace(self.token, '***TOKEN***') if self.token else str(e)
            self._log(f'API error ({method}): {err_msg}', level='error')
            return False

    @staticmethod
    def _escape(text: str) -> str:
        """Экранирует HTML-спецсимволы для Telegram."""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    def _log(self, message: str, level: str = 'info'):
        msg = str(message)
        if getattr(self, 'token', None):
            msg = msg.replace(self.token, '***TOKEN***')
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                msg, source='telegram_bot'
            )
        else:
            print(f'[TelegramBot] {msg}')
