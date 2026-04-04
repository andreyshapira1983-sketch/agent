# Telegram Monitoring Sink — отправка алертов мониторинга в Telegram
# Подключается к Monitoring (Слой 17) через параметр sink=TelegramSink(...)


import time
import threading
from collections import deque
from collections.abc import Callable
import random
import importlib

try:
    requests = importlib.import_module('requests')
except ImportError:
    requests = None

# Fallback exception for when requests is not available
_REQUEST_EXCEPTIONS = (OSError,)
if requests is not None:
    _REQUEST_EXCEPTIONS = (requests.RequestException, OSError)


class TelegramSink:
    """
    Sink для Monitoring System: отправляет WARNING/ERROR/CRITICAL в Telegram.

    Использование:
        sink = TelegramSink(token=TELEGRAM, chat_id=TELEGRAM_ALERTS_CHAT_ID)
        monitoring = Monitoring(sink=sink, min_level=LogLevel.INFO)

    Интерфейс sink:
        sink.write(entry: dict)   — Monitoring вызывает это при каждом логе
    """

    LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    LEVEL_EMOJI = {
        "DEBUG":    "🔍",
        "INFO":     "ℹ️",
        "WARNING":  "⚠️",
        "ERROR":    "❌",
        "CRITICAL": "🚨",
    }

    def __init__(
        self,
        token: str,
        chat_id: str,
        min_level: str = "ERROR",
        rate_limit_seconds: float = 5.0,
        max_queue: int = 30,
        max_retries: int = 5,
        backoff_base_seconds: float = 1.5,
        backoff_max_seconds: float = 120.0,
    ):
        """
        Args:
            token              — Telegram Bot token
            chat_id            — Chat/Channel ID для отправки
            min_level          — минимальный уровень для отправки (WARNING по умолчанию)
            rate_limit_seconds — минимальный интервал между сообщениями
            max_queue          — максимальный размер очереди сообщений
        """
        self.token = token
        self.chat_id = str(chat_id)
        self.min_level = min_level
        self.rate_limit = rate_limit_seconds
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = max(0.1, float(backoff_base_seconds))
        self.backoff_max = max(self.backoff_base, float(backoff_max_seconds))

        self._queue: deque = deque(maxlen=max_queue)
        self._last_send = 0.0
        self._lock = threading.Lock()
        self._error_count = 0
        self._dropped_count = 0
        self._stop_event = threading.Event()

        # Фоновый поток для отправки очереди
        self._scrubber: Callable[[str], str] | None = None  # SecretsRedactor

        self._worker = threading.Thread(target=self._drain_loop, daemon=True)
        self._worker.start()

    def __repr__(self) -> str:
        return f"TelegramSink(chat_id={self.chat_id}, token=***)"

    @staticmethod
    def _escape_html(text: str) -> str:
        """Экранирует HTML-спецсимволы для Telegram."""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    # ── Интерфейс sink ────────────────────────────────────────────────────────

    def write(self, entry: dict):
        """Вызывается Monitoring при каждом новом логе."""
        level = entry.get("level", "INFO")
        if not self._should_send(level):
            return
        with self._lock:
            was_full = len(self._queue) == (self._queue.maxlen or float('inf'))
            self._queue.append({
                'entry': entry,
                'attempt': 0,
                'next_try_at': 0.0,
            })
            if was_full:
                self._dropped_count += 1

    # ── Отправка ──────────────────────────────────────────────────────────────

    def send_message(self, text: str) -> bool:
        """Отправляет произвольный текст напрямую (без очереди)."""
        return self._send(text)

    def send_alert(self, title: str, body: str, level: str = "WARNING") -> bool:
        """Форматирует и отправляет алерт."""
        emoji = self.LEVEL_EMOJI.get(level, "ℹ️")
        text = f"{emoji} <b>{self._escape_html(level)}</b>\n<b>{self._escape_html(title)}</b>\n{self._escape_html(body[:800])}"
        return self._send(text)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _drain_loop(self):
        """Фоновый поток: сливает очередь с соблюдением rate limit."""
        while not self._stop_event.is_set():
            time.sleep(0.5)
            with self._lock:
                if not self._queue:
                    continue
                now = time.time()
                item = self._queue[0]
                if now < float(item.get('next_try_at', 0.0)):
                    continue
                if now - self._last_send < self.rate_limit:
                    continue
                item = self._queue.popleft()

            entry = item.get('entry', {})
            text = self._format(entry)
            if not self._send(text):
                attempt = int(item.get('attempt', 0)) + 1
                if attempt > self.max_retries:
                    self._dropped_count += 1
                    continue
                delay = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
                delay += random.uniform(0, min(1.0, delay * 0.1))
                item['attempt'] = attempt
                item['next_try_at'] = time.time() + delay
                with self._lock:
                    self._queue.appendleft(item)

    def _format(self, entry: dict) -> str:
        level = entry.get("level", "INFO")
        source = self._escape_html(str(entry.get("source", "")))
        message = self._escape_html(str(entry.get("message", "")))
        time_str = self._escape_html(str(entry.get("time_str", "")))
        emoji = self.LEVEL_EMOJI.get(level, "ℹ️")

        parts = [f"{emoji} <b>[{level}]</b>  {time_str}"]
        if source:
            parts.append(f"<i>📦 {source}</i>")
        parts.append(message[:600])
        text = "\n".join(parts)
        # SECURITY: убираем свой токен из текста, если он туда попал
        if self.token:
            text = text.replace(self.token, '***TOKEN***')
        # SECURITY: SecretsRedactor — убираем все остальные секреты
        if self._scrubber is not None:
            text = self._scrubber(text)
        return text

    def _send(self, text: str) -> bool:
        try:
            if requests is None:
                return False
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=8,
            )
            with self._lock:
                self._last_send = time.time()
            try:
                body = resp.json()
            except (ValueError, TypeError):
                body = {}
            return resp.status_code == 200 and body.get('ok', False)
        except _REQUEST_EXCEPTIONS:
            self._error_count += 1
            return False

    def stop(self):
        """Останавливает фоновый worker и даёт ему завершиться."""
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=5.0)

    def _should_send(self, level: str) -> bool:
        try:
            return (self.LEVEL_ORDER.index(level) >=
                    self.LEVEL_ORDER.index(self.min_level))
        except ValueError:
            return False
