"""
Entry point: load config, run Telegram bot loop.
Incoming message -> Memory context -> Core Intelligence -> reply -> Memory.
AUTONOMOUS_START=1 в .env — автомат (фоновые циклы) запускается сам при старте, не нужно писать /autonomous.
"""
import asyncio
import os
import sys
import threading

# Add project root for imports (must be first, before any src.* imports)
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from dotenv import load_dotenv  # noqa: E402 — must run after sys.path setup

load_dotenv(os.path.join(_root, ".env"))

_open_key = os.getenv("OPEN_KEY_API") or os.getenv("OPENAI_API_KEY")
if _open_key:
    os.environ["OPENAI_API_KEY"] = _open_key


async def _cmd_status(update, _context) -> None:
    from src.communication.telegram_commands import get_agent_status
    text = await asyncio.to_thread(get_agent_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_quality(update, _context) -> None:
    from src.communication.telegram_commands import get_quality_status
    text = await asyncio.to_thread(get_quality_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_reset_quality(update, _context) -> None:
    from src.communication.telegram_commands import reset_quality_status
    text = await asyncio.to_thread(reset_quality_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_quality_export(update, _context) -> None:
    from src.communication.telegram_commands import export_quality_status

    msg = (update.message.text if update.message and update.message.text else "").strip()
    parts = msg.split(maxsplit=1)
    report_format = "text"
    if len(parts) > 1 and parts[1].strip().lower() in ("json", "text", "txt", "full"):
        report_format = parts[1].strip().lower()
    text = await asyncio.to_thread(export_quality_status, report_format)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_log(update, context) -> None:
    from src.communication.telegram_commands import (
        get_agent_log,
        AUTONOMOUS_ACTIONS,
        MODULE_ALIASES,
        PRIORITY_ALIASES,
    )
    msg = (update.message and update.message.text) or ""
    parts = msg.split()
    n = 30
    action_filter = None
    module_filter = None
    priority_filter = None
    i = 1
    if i < len(parts) and parts[i].isdigit():
        n = min(int(parts[i]), 100)
        i += 1
    if i < len(parts) and parts[i] in AUTONOMOUS_ACTIONS:
        action_filter = parts[i]
        i += 1
    if i < len(parts) and parts[i] in MODULE_ALIASES:
        module_filter = parts[i]
        i += 1
    if i < len(parts) and parts[i] in PRIORITY_ALIASES:
        priority_filter = parts[i]
    text = await asyncio.to_thread(get_agent_log, n, action_filter, module_filter, priority_filter)
    await update.message.reply_text((text or "Нет записей.")[:4000])


async def _cmd_tasks(update, context) -> None:
    from src.communication.telegram_commands import get_queue_status
    text = await asyncio.to_thread(get_queue_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_mood(update, context) -> None:
    from src.communication.telegram_commands import get_emotion_status
    text = await asyncio.to_thread(get_emotion_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_help(update, context) -> None:
    """Список команд для пользователя."""
    from src.communication.telegram_commands import get_help_text
    text = await asyncio.to_thread(get_help_text)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_remind(update, context) -> None:
    """Напоминание: /remind завтра 18:00 купить молоко  или  /remind через 2 часа позвонить."""
    raw = ((update.message and update.message.text) or "").strip().replace("/remind", "").strip()
    if not raw:
        await update.message.reply_text(
            "Напиши: /remind завтра 18:00 купить молоко  или  /remind через 2 часа позвонить"
        )
        return
    parts = raw.split()
    when_str = ""
    reminder_text = raw
    if len(parts) >= 2 and parts[0] == "завтра":
        when_str = "завтра " + parts[1]
        reminder_text = " ".join(parts[2:])
    elif len(parts) >= 3 and parts[0] == "через":
        when_str = " ".join(parts[:3])
        reminder_text = " ".join(parts[3:])
    elif len(parts) >= 1:
        when_str = parts[0]
        reminder_text = " ".join(parts[1:])
    if not reminder_text or not when_str:
        await update.message.reply_text("Укажи время и текст: /remind завтра 18:00 купить молоко")
        return
    try:
        from src.tools.registry import call
        out = await asyncio.to_thread(call, "add_reminder", text=reminder_text, when_str=when_str)
        await update.message.reply_text(out[:1000])
    except Exception as e:
        from src.communication.user_facing_errors import user_facing_error
        await update.message.reply_text(user_facing_error(e))


async def _cmd_rate(update, _context) -> None:
    """Оценить последний ответ агента: /rate 1..5 или /rate 0..1."""
    from src.communication.telegram_commands import add_rating_from_text

    msg = (update.message.text if update.message and update.message.text else "").strip()
    parts = msg.split(maxsplit=1)
    raw_value = parts[1] if len(parts) > 1 else ""
    text = await asyncio.to_thread(add_rating_from_text, raw_value)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_cancel(update, context) -> None:
    """Остановить автономный режим (то же, что /stop)."""
    from src.communication.autonomous_mode import stop_autonomous_loop, is_autonomous_running
    if is_autonomous_running():
        stop_autonomous_loop()
        await update.message.reply_text("Остановка запрошена. Автономный режим остановится после текущего цикла.")
    else:
        await update.message.reply_text("Автономный режим не запущен. Запустить: /autonomous")


async def _cmd_guard(update, context) -> None:
    """Статус защитных контуров: patch_guard, task_guard, evolution lock, семья."""
    from src.communication.telegram_commands import get_guard_status
    text = await asyncio.to_thread(get_guard_status)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_autonomous(update, context) -> None:
    """Запустить автономный режим: циклы идут в фоне, отчёты приходят сюда в Telegram. Остановить: /stop"""
    from src.communication.autonomous_mode import start_autonomous_loop, is_autonomous_running
    if is_autonomous_running():
        await update.message.reply_text(
            "Автономный режим уже запущен. Отчёты о каждом цикле приходят сюда. Остановить: /stop"
        )
        return
    if start_autonomous_loop():
        await update.message.reply_text(
            "Автономный режим включён. Циклы идут в фоне, отчёты о каждом цикле будут приходить сюда. "
            "Остановить: /stop (остановится после текущего цикла)."
        )
    else:
        await update.message.reply_text("Не удалось запустить автономный режим.")


async def _cmd_stop(update, context) -> None:
    """Остановить автономный режим (после текущего цикла)."""
    from src.communication.autonomous_mode import stop_autonomous_loop, is_autonomous_running
    if not is_autonomous_running():
        await update.message.reply_text("Автономный режим не запущен. Запустить: /autonomous")
        return
    stop_autonomous_loop()
    await update.message.reply_text(
        "Останавливаю автономный режим (после текущего цикла). Готово — напиши /autonomous, чтобы снова запустить."
    )


async def _cmd_safe_expand(update, context) -> None:
    """Вкл/выкл: расширяться только через песочницу (propose_patch → validate → accept_patch)."""
    from src.governance.safe_expand_mode import safe_expand_enabled, set_safe_expand, get_status
    cur = safe_expand_enabled()
    set_safe_expand(not cur)
    st = get_status()
    if st["safe_expand"]:
        await update.message.reply_text(
            "Режим safe_expand включён: расширение только через песочницу (propose_patch → validate_patch → accept_patch). "
            "Повторная команда /safe_expand — выключить."
        )
    else:
        await update.message.reply_text("Режим safe_expand выключен.")


async def _cmd_apply_validated(update, context) -> None:
    """Применить все проверенные патчи из песочницы — встроить в код то, что агент уже проверил. Без программирования."""
    from src.communication.telegram_commands import get_apply_validated_result
    text = await asyncio.to_thread(get_apply_validated_result)
    await update.message.reply_text((text or "Нет данных.")[:4000])


async def _cmd_apply_sandbox_only(update, context) -> None:
    """Вкл/выкл: применять только помеченное из песочницы (в автономном режиме блокирует write_file/propose_file_edit)."""
    from src.governance.safe_expand_mode import apply_sandbox_only_enabled, set_apply_sandbox_only, get_status
    cur = apply_sandbox_only_enabled()
    set_apply_sandbox_only(not cur)
    st = get_status()
    if st["apply_sandbox_only"]:
        await update.message.reply_text(
            "Режим apply_sandbox_only включён: в автономном режиме применяется только accept_patch (validated). "
            "write_file и propose_file_edit блокируются. Повторная команда /apply_sandbox_only — выключить."
        )
    else:
        await update.message.reply_text("Режим apply_sandbox_only выключен.")


async def _handle_telegram(user_id: str, text: str) -> str:
    import time
    from src.memory.context_manager import get_context_for_llm
    from src.memory import short_term
    from src.core.intelligence import process_user_input
    from src.core.intent import interpret_intent, wrap_message_with_intent
    from src.learning.self_learning import record_exchange
    from src.monitoring.metrics import metrics
    from src.monitoring.response_verifier import enforce_verified_system_metrics
    from src.communication.telegram_commands import is_start_cycle_intent

    metrics.record_call()
    metrics.record_request_preview(text)

    low = (text or "").strip().lower()

    def _has_any(words: tuple[str, ...]) -> bool:
        return any(w in low for w in words)

    # Управление доступами обычными фразами (без slash-команд).
    if _has_any(("разрешаю интернет", "разреши интернет", "можно интернет", "доступ в интернет")):
        try:
            from src.governance.user_consent import set_internet_allowed, get_consent_status_text

            set_internet_allowed(True)
            reply = "Принял. Интернет разрешён. " + get_consent_status_text()
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", reply)
            metrics.record_success()
            return reply
        except Exception:
            pass

    if _has_any(("запрещаю интернет", "выключи интернет", "без интернета", "интернет нельзя")):
        try:
            from src.governance.user_consent import set_internet_allowed, get_consent_status_text

            set_internet_allowed(False)
            reply = "Принял. Интернет отключён. " + get_consent_status_text()
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", reply)
            metrics.record_success()
            return reply
        except Exception:
            pass

    if _has_any(("разрешаю windows", "разрешаю win", "разрешаю powershell", "разрешаю команды windows")):
        try:
            from src.governance.user_consent import set_windows_commands_allowed, get_consent_status_text

            set_windows_commands_allowed(True)
            reply = "Принял. Команды Windows разрешены. " + get_consent_status_text()
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", reply)
            metrics.record_success()
            return reply
        except Exception:
            pass

    if _has_any(("запрещаю windows", "запрещаю win", "запрещаю powershell", "windows нельзя", "команды windows нельзя")):
        try:
            from src.governance.user_consent import set_windows_commands_allowed, get_consent_status_text

            set_windows_commands_allowed(False)
            reply = "Принял. Команды Windows отключены. " + get_consent_status_text()
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", reply)
            metrics.record_success()
            return reply
        except Exception:
            pass

    if _has_any(("какие доступы", "статус доступов", "доступы", "что разрешено")):
        try:
            from src.governance.user_consent import get_consent_status_text

            reply = get_consent_status_text()
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", reply)
            metrics.record_success()
            return reply
        except Exception:
            pass

    # «Да» / «запусти» после /status — запускаем один цикл.
    if is_start_cycle_intent(text):
        try:
            from src.tools.orchestrator import Orchestrator
            orch = Orchestrator()
            summary = await asyncio.to_thread(orch.run_cycle)
            status = summary.get("status", "ok")
            if status == "quota_exceeded":
                return "Квоты исчерпаны, цикл не запущен. " + (summary.get("message") or "")
            goal = summary.get("goal", "—")
            n = summary.get("outcomes_count", 0)
            lines = [f"Цикл выполнен. Цель: {goal}", f"Действий в цикле: {n}."]
            if summary.get("improvements"):
                lines.append("Улучшения: " + ", ".join(str(x)[:50] for x in summary["improvements"][:3]))
            return "\n".join(lines)
        except Exception as e:
            return f"Ошибка при запуске цикла: {e}"

    # Человеческий small talk по памяти: приветствия и «как дела/что происходит» без команд.
    try:
        from src.communication.telegram_commands import get_human_memory_reply

        memory_reply = await asyncio.to_thread(get_human_memory_reply, user_id, text)
        if memory_reply:
            short_term.add_message(user_id, "user", text)
            short_term.add_message(user_id, "assistant", memory_reply)
            metrics.record_success()
            return memory_reply
    except Exception:
        pass

    # Natural language interface: фраза пользователя → намерение → агент выполняет действие.
    intent = await asyncio.to_thread(interpret_intent, text)
    if intent.get("command") in ("get_status", "get_metrics"):
        try:
            from src.communication.telegram_commands import get_agent_status

            return await asyncio.to_thread(get_agent_status)
        except Exception as e:
            return f"Не удалось получить статус: {e}"
    if intent.get("command") == "get_quality":
        try:
            from src.communication.telegram_commands import get_quality_status

            return await asyncio.to_thread(get_quality_status)
        except Exception as e:
            return f"Не удалось получить quality: {e}"
    if intent.get("command") in ("export_quality_text", "export_quality_json", "export_quality_full"):
        try:
            from src.communication.telegram_commands import export_quality_status

            fmt_map = {
                "export_quality_text": "text",
                "export_quality_json": "json",
                "export_quality_full": "full",
            }
            fmt = fmt_map.get(intent.get("command"), "text")
            return await asyncio.to_thread(export_quality_status, fmt)
        except Exception as e:
            return f"Не удалось выгрузить quality-отчёт: {e}"
    if intent.get("command") == "reset_quality":
        try:
            from src.communication.telegram_commands import reset_quality_status

            return await asyncio.to_thread(reset_quality_status)
        except Exception as e:
            return f"Не удалось сбросить quality-метрики: {e}"
    if intent.get("command") == "run_cycle":
        try:
            from src.tools.orchestrator import Orchestrator
            orch = Orchestrator()
            summary = await asyncio.to_thread(orch.run_cycle)
            status = summary.get("status", "ok")
            if status == "quota_exceeded":
                return "Квоты исчерпаны. " + (summary.get("message") or "")
            goal = summary.get("goal", "—")
            n = summary.get("outcomes_count", 0)
            return f"Цикл выполнен. Цель: {goal}. Действий: {n}."
        except Exception as e:
            return f"Ошибка при запуске цикла: {e}"
    text_for_agent = wrap_message_with_intent(text, intent)
    # Эмоциональные триггеры по тексту пользователя
    try:
        from src.personality.triggers import fire_trigger
        from src.personality.emotion_matrix import decay_tick
        text_lower = text.strip().lower()
        if "спать" in text_lower or "передохнуть" in text_lower or "отдых" in text_lower:
            fire_trigger("user_sleep", with_random=False)
        decay_tick()
    except Exception:
        pass
    t0 = time.perf_counter()
    try:
        context = get_context_for_llm(user_id)
        reply = await asyncio.to_thread(process_user_input, text_for_agent, context_messages=context)
        reply = enforce_verified_system_metrics(reply)
        metrics.log_time(time.perf_counter() - t0)
        metrics.record_success()
        short_term.add_message(user_id, "user", text)
        short_term.add_message(user_id, "assistant", reply)
        record_exchange(text, reply)
        # Эмоциональная окраска ответа
        try:
            from src.personality.emotional_reactions import get_emotional_flavor
            flavor = get_emotional_flavor(threshold=0.4)
            if flavor and len(reply) + len(flavor) < 3900:
                reply = reply + "\n\n" + flavor
        except Exception:
            pass
        # Редкая «фантазийная» фраза (скука, хочу создать агента/семейку)
        try:
            from src.personality.personality import get_whim_from_emotions
            whim = get_whim_from_emotions()
            if whim and len(reply) + len(whim) < 3900:
                reply = reply + "\n\n" + whim
        except Exception:
            pass
        return reply
    except Exception as e:
        metrics.record_error(str(e)[:100])
        try:
            from src.personality.triggers import fire_trigger
            fire_trigger("user_criticism", with_random=False)
        except Exception:
            pass
        raise


def _check_telegram_token(token: str) -> bool:
    """Проверка токена через HTTP, без asyncio — иначе на Windows event loop закрыт до run_polling."""
    import urllib.request
    import json

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # nosec B310 — Telegram API URL
            data = json.loads(resp.read().decode())
        return data.get("ok") is True
    except Exception:
        return False


def main() -> None:
    from src.communication.telegram_client import set_default_handler, run_bot
    from src.knowledge.store import seed_architecture_docs

    set_default_handler(_handle_telegram)
    # Документы архитектуры — в системный контекст (prompt) и в Knowledge Store при первом запуске
    n = seed_architecture_docs(_root)
    if n > 0:
        print(f"Knowledge Store: добавлено {n} архитектурных документов.")
    # Опционально: индекс кода + эмбеддинги в фоне (один раз). Агент сам будет искать модули по смыслу.
    if (os.getenv("BUILD_CODE_INDEX") or "").strip().lower() in ("1", "true", "yes"):
        def _build_index():
            try:
                from src.knowledge.code_index import build_code_index
                from pathlib import Path
                n = build_code_index(root=Path(_root), dirs=["src"], with_embeddings=True)
                print(f"Индекс кода: {n} модулей, эмбеддинги готовы (поиск по коду работает).")
            except Exception as e:
                print("Индекс кода не собран:", e)
        _index_thread = threading.Thread(target=_build_index, daemon=True, name="build-code-index")
        _index_thread.start()
    # Опционально: веб-дашборд с графом состояния агента (http://127.0.0.1:8765/dashboard/)
    if (os.getenv("DASHBOARD") or "").strip() in ("1", "true", "yes"):
        try:
            from src.hitl.dashboard_server import start_dashboard_thread
            start_dashboard_thread()
            print("Dashboard: http://127.0.0.1:8765/dashboard/ (порт можно задать через DASHBOARD_PORT)")
        except Exception as e:
            print("Dashboard не запущен:", e)
    # Токен Telegram = вся строка (число:строка), не отрезать часть до двоеточия
    token = os.getenv("TELEGRAM", "").strip()
    if not token:
        print("TELEGRAM token not set in .env")
        return
    if not _check_telegram_token(token):
        ln = len(token)
        print(
            f"Ошибка: Telegram отклонил токен бота (длина: {ln} символов).\n"
            "В .env переменная TELEGRAM должна содержать токен целиком, как у @BotFather (число:строка)."
        )
        sys.exit(1)
    if not (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip():
        print("Алерты в Telegram будут приходить в тот же чат, где ты общаешься с ботом. (Опционально: TELEGRAM_ALERTS_CHAT_ID в .env — другой чат.)")
    else:
        # Приветствие первым: через пару секунд после старта отправить «Доброе утро» / «Добрый день» и т.д.
        def _send_greeting_later():
            import time
            time.sleep(3)
            try:
                from src.communication.telegram_alerts import send_startup_greeting
                if send_startup_greeting():
                    print("Приветствие отправлено в чат (TELEGRAM_ALERTS_CHAT_ID).")
            except Exception as e:
                print("Приветствие при старте не отправлено:", e)
        _greeting_thread = threading.Thread(target=_send_greeting_later, daemon=True, name="startup-greeting")
        _greeting_thread.start()
    # Автозапуск автомата: циклы сами идут в фоне, отчёты приходят в Telegram. Остановить: /stop
    if (os.getenv("AUTONOMOUS_START") or "").strip().lower() in ("1", "true", "yes"):
        def _run_autonomous_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from src.communication.autonomous_mode import run_autonomous_loop
                loop.run_until_complete(run_autonomous_loop())
            finally:
                loop.close()
        _autonomous_thread = threading.Thread(target=_run_autonomous_loop, daemon=True, name="autonomous-loop")
        _autonomous_thread.start()
        print("Автономный режим (автомат) запущен при старте. Отчёты о циклах будут приходить в Telegram. Остановить: /stop")
    def _quality_summary_loop():
        import time
        while True:
            try:
                from src.communication.telegram_alerts import send_daily_quality_summary_if_due
                send_daily_quality_summary_if_due()
            except Exception:
                pass
            time.sleep(1800)
    _quality_summary_thread = threading.Thread(target=_quality_summary_loop, daemon=True, name="quality-summary-loop")
    _quality_summary_thread.start()
    run_bot(
        token,
        help_handler=_cmd_help,
        cancel_handler=_cmd_cancel,
        remind_handler=_cmd_remind,
        rate_handler=_cmd_rate,
        status_handler=_cmd_status,
        quality_handler=_cmd_quality,
        quality_export_handler=_cmd_quality_export,
        reset_quality_handler=_cmd_reset_quality,
        log_handler=_cmd_log,
        tasks_handler=_cmd_tasks,
        mood_handler=_cmd_mood,
        guard_handler=_cmd_guard,
        autonomous_handler=_cmd_autonomous,
        stop_handler=_cmd_stop,
        safe_expand_handler=_cmd_safe_expand,
        apply_sandbox_only_handler=_cmd_apply_sandbox_only,
        apply_validated_handler=_cmd_apply_validated,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБот остановлен (Ctrl+C).")
        sys.exit(0)
