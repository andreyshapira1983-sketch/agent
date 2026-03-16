"""
Отправка алертов и ошибок в Telegram.
Если TELEGRAM_ALERTS_CHAT_ID не задан — алерты уходят в тот же чат, где ты уже общаешься с ботом (ничего настраивать не нужно).
"""
from __future__ import annotations

import logging
import os
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_log = logging.getLogger(__name__)

_MAX_MESSAGE_LEN = 4000
_last_autonomous_sent: float = 0.0
_last_chat_id: str | None = None  # чат, куда писали боту — сюда же шлём алерты, если TELEGRAM_ALERTS_CHAT_ID не задан
_logged_404_once = False  # чтобы не спамить в консоль при повторных 404
_SUMMARY_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "quality_summary_state.json"


def set_last_chat_id(chat_id: str) -> None:
    """Запомнить чат (вызывается при каждом сообщении пользователя). Алерты пойдут сюда, если .env не настроен."""
    global _last_chat_id
    _last_chat_id = chat_id


def _get_token() -> str:
    """Токен для Telegram Bot API = вся строка из .env (как даёт BotFather: число:строка)."""
    return (os.getenv("TELEGRAM") or "").strip().split("\n")[0].strip()


def _send_to_alerts_chat(text: str) -> bool:
    """Отправить текст в чат алертов. Chat: из .env или последний чат, где писали боту."""
    token = _get_token()
    chat_id = (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip() or _last_chat_id
    if not token or not chat_id:
        return False
    msg = (text or "").strip()
    if not msg:
        return False
    if len(msg) > _MAX_MESSAGE_LEN:
        msg = msg[: _MAX_MESSAGE_LEN - 3] + "..."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        global _logged_404_once
        if e.code == 404:
            if not _logged_404_once:
                _logged_404_once = True
                _log.warning(
                    "Telegram alerts: HTTP 404. Возможные причины: (1) неверный/отозванный токен — в @BotFather /mybots → API Token; "
                    "(2) неверный TELEGRAM_ALERTS_CHAT_ID — для лички это твой user id (напиши боту, в консоли подскажет id); "
                    "(3) бот не добавлен в группу или блокировка API (попробуй VPN)."
                )
        else:
            _log.warning("Telegram alerts: HTTP %s %s", e.code, e.reason)
    except Exception as e:
        _log.warning("Telegram alerts: %s", e)
    return False


def get_alerts_chat_id() -> str | None:
    """Чат, куда уходят алерты и проактивные сообщения (для отправки медиа)."""
    return (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip() or _last_chat_id


def _greeting_by_time() -> str:
    """Приветствие по времени суток: утро/день/вечер/ночь."""
    h = time.gmtime(time.time()).tm_hour
    # локальное время через datetime проще для пользователя
    try:
        from datetime import datetime
        h = datetime.now().hour
    except Exception:
        pass
    if 5 <= h < 12:
        return "Доброе утро!"
    if 12 <= h < 18:
        return "Добрый день!"
    if 18 <= h < 23:
        return "Добрый вечер!"
    return "Привет!"


def send_startup_greeting() -> bool:
    """
    При старте бота — одно сообщение пользователю. Сначала агент сам решает (ход инициативы);
    если ничего не сказал — приветствие по времени суток.
    Чтобы бот писал первым, в .env задай TELEGRAM_ALERTS_CHAT_ID.
    """
    chat_id = (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip() or _last_chat_id
    if not chat_id:
        return False
    try:
        from src.communication.proactive_planner import agent_initiative_message
        msg = agent_initiative_message()
    except Exception:
        msg = None
    if not msg:
        msg = _greeting_by_time()
    return _send_to_alerts_chat(msg)


def send_alert(text: str) -> None:
    """
    Отправить сообщение в чат алертов (если настроен).
    Вызывается при tool_slow, performance_alert, проактивных сообщениях.
    Не бросает исключений — ошибки логируются.
    """
    _send_to_alerts_chat(text)


def send_daily_quality_summary_if_due(force: bool = False, now_struct: time.struct_time | None = None) -> bool:
    """
    Раз в сутки отправить сводку качества в Telegram.
    Если force=True — отправить без проверки даты.
    """
    chat_id = get_alerts_chat_id()
    if not chat_id:
        return False
    now = now_struct or time.localtime()
    today = f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d}"
    if not force and _SUMMARY_STATE_PATH.exists():
        try:
            raw = json.loads(_SUMMARY_STATE_PATH.read_text(encoding="utf-8"))
            if raw.get("last_sent_date") == today:
                return False
        except Exception:
            pass
    try:
        from src.communication.telegram_commands import get_weekly_quality_summary
        text = get_weekly_quality_summary()
    except Exception as e:
        _log.debug("daily quality summary: %s", e)
        return False
    if not _send_to_alerts_chat(text):
        return False
    try:
        _SUMMARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SUMMARY_STATE_PATH.write_text(json.dumps({"last_sent_date": today}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return True


def send_proactive_voice(voice_path: str) -> bool:
    """
    Отправить голосовое сообщение в чат алертов (проактивно).
    voice_path — путь к .ogg/.mp3. Использует Telegram sendVoice.
    """
    token = _get_token()
    chat_id = get_alerts_chat_id()
    if not token or not chat_id:
        return False
    path = __import__("pathlib").Path(voice_path)  # noqa: pathlib for path.exists
    if not path.exists() or not path.is_file():
        return False
    url = f"https://api.telegram.org/bot{token}/sendVoice"
    try:
        with open(path, "rb") as f:
            data = f.read()
        boundary = "----FormBoundary" + str(time.time_ns())
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"voice.ogg\"\r\n"
            f"Content-Type: audio/ogg\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        _log.debug("send_proactive_voice: %s", e)
        return False


def send_autonomous_event(
    text: str,
    urgent: bool = False,
    emotion_context: dict[str, str | int] | None = None,
) -> None:
    """
    Уведомление об автономном действии агента в Telegram.
    emotion_context — контекст для эмоциональной окраски (improvements, patch_failed и т.д.).
    """
    global _last_autonomous_sent
    if (os.getenv("TELEGRAM_AUTONOMOUS_EVENTS") or "1").strip().lower() in ("0", "false", "no"):
        return
    chat_id = (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip() or _last_chat_id
    if not chat_id:
        return
    msg = (text or "").strip()
    if not msg:
        return
    try:
        from src.personality.emotional_reactions import get_emotional_flavor_for_notification
        flavor = get_emotional_flavor_for_notification(emotion_context)
        if flavor:
            msg = flavor + "\n" + msg
    except Exception:
        pass
    interval_sec = 0.0
    try:
        interval_sec = float((os.getenv("TELEGRAM_ALERTS_MIN_INTERVAL_SEC") or "60").strip())
    except ValueError:
        pass
    if not urgent and interval_sec > 0:
        now = time.monotonic()
        if now - _last_autonomous_sent < interval_sec:
            return
    if _send_to_alerts_chat(msg):
        if not urgent and interval_sec > 0:
            _last_autonomous_sent = time.monotonic()


_last_step_sent: float = 0.0


def send_agent_step(event_type: str, message: str) -> None:
    """
    Короткое уведомление о шаге агента в Telegram (Начал X, Применил патч, Сгенерировал отчёт).
    Отправляется только если event_type входит в TELEGRAM_EVENT_TYPES (через запятую):
    cycle, goal, task_start, patch, report.
    По умолчанию ничего не шлём; чтобы получать шаги: TELEGRAM_EVENT_TYPES=task_start,patch,report
    или TELEGRAM_EVENT_TYPES=cycle,goal,task_start,patch,report.
    """
    if (os.getenv("TELEGRAM_AUTONOMOUS_EVENTS") or "1").strip().lower() in ("0", "false", "no"):
        return
    allowed_raw = (os.getenv("TELEGRAM_EVENT_TYPES") or "").strip()
    if not allowed_raw:
        return
    allowed = [x.strip().lower() for x in allowed_raw.split(",") if x.strip()]
    if event_type.strip().lower() not in allowed:
        return
    chat_id = (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip() or _last_chat_id
    if not chat_id:
        return
    msg = (message or "").strip()
    if not msg:
        return
    if len(msg) > _MAX_MESSAGE_LEN:
        msg = msg[: _MAX_MESSAGE_LEN - 3] + "..."
    step_interval_sec = 10.0
    try:
        step_interval_sec = float((os.getenv("TELEGRAM_STEP_INTERVAL_SEC") or "10").strip())
    except ValueError:
        pass
    global _last_step_sent
    now = time.monotonic()
    if now - _last_step_sent < step_interval_sec:
        return
    if _send_to_alerts_chat(msg):
        _last_step_sent = now
