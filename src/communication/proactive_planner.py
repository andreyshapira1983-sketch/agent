"""
Проактивная отправка: агент сам инициирует диалог.
Один общий механизм: «ход инициативы» — агент получает контекст (время, цель, действие) и сам решает,
сказать ли что-то и что именно. Приветствие, отчёт, вопрос — результат его решения, а не отдельные правила.
Лимиты: TELEGRAM_PROACTIVE_MIN_INTERVAL_SEC, TELEGRAM_PROACTIVE_MAX_PER_DAY.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

_log = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "proactive_state.json"
_DEFAULT_MIN_INTERVAL_SEC = 6 * 3600  # 6 часов
_DEFAULT_MAX_PER_DAY = 3


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        _log.debug("proactive save_state: %s", e)


def _get_config() -> tuple[float, int, float]:
    interval = _DEFAULT_MIN_INTERVAL_SEC
    try:
        v = (os.getenv("TELEGRAM_PROACTIVE_MIN_INTERVAL_SEC") or "").strip()
        if v:
            interval = max(60.0, float(v))
    except ValueError:
        pass
    max_per_day = _DEFAULT_MAX_PER_DAY
    try:
        v = (os.getenv("TELEGRAM_PROACTIVE_MAX_PER_DAY") or "").strip()
        if v:
            max_per_day = max(1, min(20, int(v)))
    except ValueError:
        pass
    # Температура инициативы (0..1): вероятность попытаться отправить проактивное сообщение при разрешённом интервале. 0 = никогда, 1 = всегда.
    initiative_temp = 1.0
    try:
        v = (os.getenv("INITIATIVE_TEMPERATURE") or "").strip()
        if v:
            initiative_temp = max(0.0, min(1.0, float(v)))
    except ValueError:
        pass
    return interval, max_per_day, initiative_temp


def _message_from_reading_log() -> str | None:
    """Сформировать короткое проактивное сообщение из последней записи лога прочитанного."""
    try:
        from src.tools.registry import call
        raw = call("get_reading_log", limit=1)
        if not raw or "Лог прочитанного пуст" in raw or "Записей нет" in raw:
            return None
        lines = raw.strip().split("\n")
        if not lines:
            return None
        title = lines[0].strip()
        if title.startswith("1. "):
            title = title[2:].strip()
        if len(title) > 80:
            title = title[:77] + "..."
        return f"Недавно прочитал: {title}. Можем обсудить, если интересно."
    except Exception:
        return None


def _gather_initiative_context() -> str:
    """Собрать контекст для «хода инициативы»: время, последняя цель, последнее действие, эмоция."""
    from datetime import datetime
    now = datetime.now()
    h = now.hour
    if 5 <= h < 12:
        time_desc = "утро"
    elif 12 <= h < 18:
        time_desc = "день"
    elif 18 <= h < 23:
        time_desc = "вечер"
    else:
        time_desc = "ночь"
    time_str = now.strftime("%Y-%m-%d %H:%M")
    last_goal = ""
    last_action = ""
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(80)
        for e in reversed(tail):
            if e.get("action") == "autonomous_cycle_end":
                d = e.get("details") or {}
                last_goal = str(d.get("goal") or "").strip()
                break
        for e in reversed(tail):
            if e.get("action") == "autonomous_act":
                d = e.get("details") or {}
                last_action = f"{d.get('tool', '')}"
                break
    except Exception:
        pass
    emotion_hint = ""
    try:
        from src.personality.emotion_matrix import get_dominant, get_intensity
        dom = get_dominant()
        intense = get_intensity()
        if dom:
            emotion_hint = f", эмоциональный тон: {dom} (интенсивность {intense})"
    except Exception:
        pass
    return (
        f"Сейчас: {time_str} ({time_desc}). "
        f"Последняя цель цикла: {last_goal or '—'}. Последнее действие: {last_action or '—'}{emotion_hint}."
    )


def agent_initiative_message() -> str | None:
    """
    Один общий «ход инициативы»: агент сам решает, сказать ли что-то пользователю.
    Получает контекст (время, цель, действие) и возвращает одно короткое сообщение или None.
    Не проверяет лимиты — это делает вызывающий код.
    """
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return None
        ctx = _gather_initiative_context()
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты автономный агент. Тебе дают контекст. Ты можешь отправить пользователю одно короткое сообщение: "
                        "приветствие, что делаешь/сделал, вопрос или мысль. Или решить молчать. "
                        "Ответь только текстом сообщения (одна-две фразы) или ровно NOTHING, если не хочешь ничего писать."
                    ),
                },
                {"role": "user", "content": f"Контекст: {ctx}\nНапиши сообщение пользователю или NOTHING."},
            ],
            max_tokens=120,
        )
        if not r.choices or not r.choices[0].message.content:
            return None
        text = (r.choices[0].message.content or "").strip()
        if not text or text.upper().strip() == "NOTHING" or len(text) < 2:
            return None
        return text[:400]
    except Exception as e:
        _log.debug("agent_initiative_message: %s", e)
        return None


def _message_from_llm() -> str | None:
    """Одно короткое предложение от LLM для начала разговора, или None (legacy fallback)."""
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return None
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Ты агент. Сгенерируй одно короткое предложение, чтобы начать разговор с пользователем: вопрос, мысль, идея. Только текст, без кавычек. Если нечего сказать, ответь ровно NO.",
                },
                {"role": "user", "content": "Что сказать пользователю?"},
            ],
            max_tokens=80,
        )
        if not r.choices or not r.choices[0].message.content:
            return None
        text = (r.choices[0].message.content or "").strip()
        if not text or text.upper() == "NO" or len(text) < 3:
            return None
        return text[:300]
    except Exception:
        return None


def _record_initiative_silent(reason: str) -> None:
    try:
        from src.hitl.initiative_visor_state import set_initiative_result
        set_initiative_result(False, message_preview="", silent_reason=reason)
    except Exception:
        pass


def get_proactive_message() -> str | None:
    """
    Решить, есть ли что отправить проактивно. Учитывает INITIATIVE_TEMPERATURE (вероятность попытки).
    Сначала — «ход инициативы» (агент сам решает); иначе fallback: лог прочитанного или LLM.
    При отказе записывает причину для визора инициативы на дашборде.
    """
    interval_sec, max_per_day, initiative_temp = _get_config()
    if initiative_temp <= 0:
        _record_initiative_silent("temperature=0")
        return None
    if initiative_temp < 1.0 and (os.urandom(1)[0] / 255.0) > initiative_temp:
        _record_initiative_silent("temperature_skip")
        return None
    state = _load_state()
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    last_ts = state.get("last_sent_ts") or 0
    if now - last_ts < interval_sec:
        _record_initiative_silent("interval")
        return None

    date = state.get("date", "")
    count = state.get("count_today", 0)
    if date != today:
        count = 0
    if count >= max_per_day:
        _record_initiative_silent("limit_per_day")
        return None

    # Сначала даём слово агенту: он сам решает, что сказать (приветствие, отчёт, вопрос).
    msg = agent_initiative_message()
    if not msg:
        msg = _message_from_reading_log()
    if not msg:
        msg = _message_from_llm()
    if not msg:
        _record_initiative_silent("agent_said_nothing")
        return None

    return msg


def try_send_proactive() -> bool:
    """
    Если прошло достаточно времени и лимиты не исчерпаны — сформировать сообщение и отправить в чат алертов.
    Результат (отправил/молчал и почему) пишется в визор инициативы для дашборда.
    """
    msg = get_proactive_message()
    if not msg:
        return False
    try:
        from src.communication.telegram_alerts import send_alert
        send_alert(msg)
    except Exception as e:
        _log.debug("proactive send_alert: %s", e)
        try:
            from src.hitl.initiative_visor_state import set_initiative_result
            set_initiative_result(False, message_preview="", silent_reason="send_failed")
        except Exception:
            pass
        return False
    try:
        from src.hitl.initiative_visor_state import set_initiative_result
        set_initiative_result(True, message_preview=msg[:200], silent_reason="")
    except Exception:
        pass
    state = _load_state()
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    state["last_sent_ts"] = now
    state["date"] = today
    state["count_today"] = state.get("count_today", 0) if state.get("date") == today else 0
    state["count_today"] = state["count_today"] + 1
    _save_state(state)
    return True
