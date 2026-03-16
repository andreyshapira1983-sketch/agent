"""
Telegram bot: long polling, receive messages -> Core Intelligence -> send reply.
Команды: /status, /log, /help, /cancel — см. run_bot(...).
Сообщения обрабатываются по одному на чат (очередь по chat_id). Ошибки — понятные пользователю.
Файлы сохраняются в data/received_files. Голос → Whisper → текст. Фото и видео → CV → агент.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Awaitable

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler as TgMessageHandler,
    CommandHandler,
    filters,
)

# Каталог для сохранения полученных файлов и видео (относительно корня проекта)
_project_root = Path(__file__).resolve().parent.parent.parent
RECEIVED_FILES_DIR = _project_root / "data" / "received_files"

# Handler signature: (user_id: str, text: str) -> reply: str
AsyncHandler = Callable[[str, str], Awaitable[str]]
# Command handler: (update, context) -> None (reply inside)
CommandCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

_default_handler: AsyncHandler | None = None
_chat_id_hint_printed = False
_log = logging.getLogger(__name__)

# Блокировка по chat_id: только одно сообщение в чате обрабатывается одновременно
_chat_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()


async def _get_chat_lock(chat_id: str) -> asyncio.Lock:
    async with _locks_lock:
        if chat_id not in _chat_locks:
            _chat_locks[chat_id] = asyncio.Lock()
        return _chat_locks[chat_id]


async def _get_media_file_or_report(update: Update, media: object, media_label: str):
    """Safely resolve Telegram file and return None on user-facing errors."""
    if not update.message:
        return None
    try:
        return await media.get_file()  # type: ignore[attr-defined]
    except BadRequest as e:
        msg = str(e)
        if "File is too big" in msg:
            await update.message.reply_text(
                f"{media_label} слишком большой для загрузки ботом Telegram. "
                "Отправьте файл меньшего размера или сжатую версию."
            )
            return None
        await update.message.reply_text(f"Не удалось получить {media_label.lower()}: {msg}")
        return None
    except TelegramError as e:
        await update.message.reply_text(f"Ошибка Telegram при получении {media_label.lower()}: {e}")
        return None


def _is_voice_reply_requested(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return t.startswith("🎤") or t.startswith("/voice")


async def _run_handler_with_reply(
    update: Update,
    user_id: str,
    text: str,
    *,
    prefer_voice: bool = False,
    fallback_no_handler: str = "Обработчик не подключён.",
) -> None:
    """Отправить просроченные напоминания, вызвать handler, ответить или показать понятную ошибку."""
    try:
        from src.tools.impl.browser_reminder_tools import get_due_reminders
        for r in get_due_reminders():
            reminder_text = (r.get("text") or "").strip()
            if reminder_text and update.message:
                await update.message.reply_text("⏰ " + reminder_text[:1000])
    except Exception:
        pass
    handler = _default_handler
    if not handler:
        await update.message.reply_text(fallback_no_handler) if update.message else None
        return
    try:
        reply = await handler(user_id, text)
        await _reply_with_optional_voice(update, reply, prefer_voice=prefer_voice)
    except Exception as e:
        from src.communication.user_facing_errors import user_facing_error
        await update.message.reply_text(user_facing_error(e)) if update.message else None


async def _reply_with_optional_voice(update: Update, reply_text: str, *, prefer_voice: bool = False) -> None:
    if not update.message:
        return
    reply = (reply_text or "").strip()
    if not reply:
        reply = ""
    if prefer_voice and reply:
        try:
            from src.communication.tts import synthesize

            audio_path = Path(await asyncio.to_thread(synthesize, reply))
            if audio_path.exists() and audio_path.is_file():
                with audio_path.open("rb") as f:
                    try:
                        await update.message.reply_voice(voice=f)
                        return
                    except TelegramError:
                        pass
                with audio_path.open("rb") as f:
                    await update.message.reply_audio(audio=f)
                return
        except Exception:
            pass
    await update.message.reply_text(reply[:4000] if reply else "")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled exceptions to keep polling alive and diagnosable."""
    _log.exception("Unhandled Telegram error", exc_info=context.error)


def set_default_handler(handler: AsyncHandler) -> None:
    global _default_handler
    _default_handler = handler


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    chat_id = str(update.effective_chat.id if update.effective_chat else "")
    if chat_id:
        try:
            from src.communication.telegram_alerts import set_last_chat_id
            set_last_chat_id(chat_id)
            global _chat_id_hint_printed
            if not _chat_id_hint_printed:
                import os
                if not (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip():
                    print(f"Для алертов в этот чат добавь в .env: TELEGRAM_ALERTS_CHAT_ID={chat_id}")
                    _chat_id_hint_printed = True
        except Exception:
            pass
    user_id = str(update.effective_user.id if update.effective_user else "")
    text = update.message.text.strip()
    prefer_voice = _is_voice_reply_requested(text)
    handler = _default_handler

    async def _do_handle() -> None:
        await _run_handler_with_reply(update, user_id, text, prefer_voice=prefer_voice)

    if chat_id:
        lock = await _get_chat_lock(chat_id)
        async with lock:
            await _do_handle()
    else:
        await _do_handle()


async def _handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохраняет полученные документы, фото, видео в data/received_files. Фото/видео → CV → текст → агент."""
    if not update.message:
        return
    RECEIVED_FILES_DIR.mkdir(parents=True, exist_ok=True)
    # Фото: Image → Vision → описание → агент (как с видео)
    if update.message.photo:
        photos = update.message.photo
        file = await _get_media_file_or_report(update, photos[-1], "Фото")  # наибольший размер
        if file is None:
            return
        name = f"photo_{photos[-1].file_unique_id}.jpg"
        path = RECEIVED_FILES_DIR / name
        await file.download_to_drive(path)
        caption = (update.message.caption or "").strip()
        try:
            from src.communication.video_vision import describe_image
            description = await asyncio.to_thread(describe_image, path)
        except Exception:
            description = ""
        text = "[Фото] " + (caption or "Пользователь прислал фото.")
        if description:
            text += " Описание: " + description
        else:
            text += " Файл сохранён: " + name
        text += " (Человек на фото — это отправитель, с которым ты общаешься.)"
        chat_id = str(update.effective_chat.id if update.effective_chat else "")
        if chat_id:
            try:
                from src.communication.telegram_alerts import set_last_chat_id
                set_last_chat_id(chat_id)
            except Exception:
                pass
        user_id = str(update.effective_user.id if update.effective_user else "")
        if chat_id:
            lock = await _get_chat_lock(chat_id)
            async with lock:
                await _run_handler_with_reply(update, user_id, text, fallback_no_handler=f"Фото сохранено: {name}")
        else:
            await _run_handler_with_reply(update, user_id, text, fallback_no_handler=f"Фото сохранено: {name}")
        return
    if update.message.document:
        file = await _get_media_file_or_report(update, update.message.document, "Документ")
        if file is None:
            return
        name = update.message.document.file_name or "document"
        path = RECEIVED_FILES_DIR / name
        await file.download_to_drive(path)
        await update.message.reply_text(f"Файл сохранён: {name}")
        return
    # Обычное видео или видеозаметка (кружок) — качаем, кадр → vision → агент
    video_obj = getattr(update.message, "video", None) or getattr(update.message, "video_note", None)
    if video_obj:
        file = await _get_media_file_or_report(update, video_obj, "Видео")
        if file is None:
            return
        name = f"video_{video_obj.file_unique_id}.mp4"
        path = RECEIVED_FILES_DIR / name
        await file.download_to_drive(path)
        caption = (update.message.caption or "").strip()
        try:
            from src.communication.video_vision import describe_video_frame, transcribe_video_audio
            description = await asyncio.to_thread(describe_video_frame, path)
            speech = await asyncio.to_thread(transcribe_video_audio, path)
        except Exception as e:
            _log.debug("video_vision: %s", e)
            description = ""
            speech = ""
        kind = "видеозаметку" if getattr(update.message, "video_note", None) else "видео"
        text = "[Видео] " + (caption or f"Пользователь прислал {kind}.")
        if description:
            text += " Описание кадра: " + description
        else:
            text += " Файл сохранён: " + name
        if speech:
            text += " Что сказано в видео (расшифровка речи): «" + speech + "»"
        text += " (Человек на видео — это отправитель, с которым ты общаешься.)"
        chat_id = str(update.effective_chat.id if update.effective_chat else "")
        if chat_id:
            try:
                from src.communication.telegram_alerts import set_last_chat_id
                set_last_chat_id(chat_id)
            except Exception:
                pass
        user_id = str(update.effective_user.id if update.effective_user else "")
        if chat_id:
            lock = await _get_chat_lock(chat_id)
            async with lock:
                await _run_handler_with_reply(update, user_id, text, fallback_no_handler=f"Видео сохранено: {name}")
        else:
            await _run_handler_with_reply(update, user_id, text, fallback_no_handler=f"Видео сохранено: {name}")
        return
    if update.message.voice:
        file = await _get_media_file_or_report(update, update.message.voice, "Голосовое сообщение")
        if file is None:
            return
        name = f"voice_{update.message.voice.file_unique_id}.ogg"
        path = RECEIVED_FILES_DIR / name
        await file.download_to_drive(path)
        # Распознать речь и обработать как обычное сообщение
        from src.communication.whisper_stt import transcribe_audio
        text = await asyncio.to_thread(transcribe_audio, path)
        if not text or text.startswith("("):
            await update.message.reply_text(f"Голос сохранён: {name}. Распознавание: {text or 'пусто'}")
            return
        # Тот же поток, что и для текста: chat_id, handler, ответ
        chat_id = str(update.effective_chat.id if update.effective_chat else "")
        if chat_id:
            try:
                from src.communication.telegram_alerts import set_last_chat_id
                set_last_chat_id(chat_id)
            except Exception:
                pass
        user_id = str(update.effective_user.id if update.effective_user else "")
        voice_text = f"[Голос] {text}"
        if chat_id:
            lock = await _get_chat_lock(chat_id)
            async with lock:
                await _run_handler_with_reply(update, user_id, voice_text, prefer_voice=True)
        else:
            await _run_handler_with_reply(update, user_id, voice_text, prefer_voice=True)


def _wrap_set_chat(handler: CommandCallback) -> CommandCallback:
    """Обновить last_chat_id перед вызовом обработчика, чтобы алерты шли в этот чат."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat:
            try:
                from src.communication.telegram_alerts import set_last_chat_id
                set_last_chat_id(str(update.effective_chat.id))
            except Exception:
                pass
        await handler(update, context)
    return wrapped


def run_bot(
    token: str,
    *,
    status_handler: CommandCallback | None = None,
    quality_handler: CommandCallback | None = None,
    quality_export_handler: CommandCallback | None = None,
    reset_quality_handler: CommandCallback | None = None,
    log_handler: CommandCallback | None = None,
    tasks_handler: CommandCallback | None = None,
    mood_handler: CommandCallback | None = None,
    autonomous_handler: CommandCallback | None = None,
    stop_handler: CommandCallback | None = None,
    safe_expand_handler: CommandCallback | None = None,
    apply_sandbox_only_handler: CommandCallback | None = None,
    apply_validated_handler: CommandCallback | None = None,
    guard_handler: CommandCallback | None = None,
    help_handler: CommandCallback | None = None,
    cancel_handler: CommandCallback | None = None,
    remind_handler: CommandCallback | None = None,
    rate_handler: CommandCallback | None = None,
) -> None:
    """Run Telegram bot with long polling. Optional: /status, /quality, /quality_export, /reset_quality, /log, /tasks, /mood, /autonomous, /stop, /safe_expand, /apply_sandbox_only, /guard, /help, /cancel, /remind, /rate."""
    app = Application.builder().token(token).build()
    app.add_error_handler(_on_error)
    app.add_handler(TgMessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(TgMessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VIDEO_NOTE | filters.VOICE, _handle_file))
    if help_handler:
        app.add_handler(CommandHandler("help", _wrap_set_chat(help_handler)))
    if cancel_handler:
        app.add_handler(CommandHandler("cancel", _wrap_set_chat(cancel_handler)))
    if remind_handler:
        app.add_handler(CommandHandler("remind", _wrap_set_chat(remind_handler)))
    if rate_handler:
        app.add_handler(CommandHandler("rate", _wrap_set_chat(rate_handler)))
    if status_handler:
        app.add_handler(CommandHandler("status", _wrap_set_chat(status_handler)))
    if quality_handler:
        app.add_handler(CommandHandler("quality", _wrap_set_chat(quality_handler)))
    if quality_export_handler:
        app.add_handler(CommandHandler("quality_export", _wrap_set_chat(quality_export_handler)))
    if reset_quality_handler:
        app.add_handler(CommandHandler("reset_quality", _wrap_set_chat(reset_quality_handler)))
    if log_handler:
        app.add_handler(CommandHandler("log", _wrap_set_chat(log_handler)))
    if tasks_handler:
        app.add_handler(CommandHandler("tasks", _wrap_set_chat(tasks_handler)))
        app.add_handler(CommandHandler("queue", _wrap_set_chat(tasks_handler)))
    if mood_handler:
        app.add_handler(CommandHandler("mood", _wrap_set_chat(mood_handler)))
        app.add_handler(CommandHandler("emotions", _wrap_set_chat(mood_handler)))
    if guard_handler:
        app.add_handler(CommandHandler("guard", _wrap_set_chat(guard_handler)))
    if autonomous_handler:
        app.add_handler(CommandHandler("autonomous", _wrap_set_chat(autonomous_handler)))
    if stop_handler:
        app.add_handler(CommandHandler("stop", _wrap_set_chat(stop_handler)))
    if safe_expand_handler:
        app.add_handler(CommandHandler("safe_expand", _wrap_set_chat(safe_expand_handler)))
    if apply_sandbox_only_handler:
        app.add_handler(CommandHandler("apply_sandbox_only", _wrap_set_chat(apply_sandbox_only_handler)))
    if apply_validated_handler:
        app.add_handler(CommandHandler("apply_validated", _wrap_set_chat(apply_validated_handler)))
    app.run_polling(allowed_updates=Update.ALL_TYPES)
