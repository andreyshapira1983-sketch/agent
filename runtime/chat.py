"""
runtime/chat.py — Human-style chat handler.

Routes one inbound message into one of two paths:

    short text, no attachment     →  Brain.think() → plain-text reply
    attachment OR job-shaped text →  Brain.intake_job() → workflow → deliver

The reply that goes back to the user is ALWAYS plain text. Internals
like JSON, ThinkResult fields, policy verdicts, and confidences never
leave the runtime — those live in the audit log.

Design notes
────────────
- One `ChatHandler` is shared across both Telegram and (eventually) any
  future chat surface. The handler is channel-agnostic — it takes a
  brief + client_id, returns a `ChatReply`.
- Brain interactions go through a stable `session_id` per client so the
  conversation keeps context across messages from the same user.
- We deliberately never echo the user's message back, never prefix
  replies with "[Agent]" or "[ThinkResult]", never inject confidence or
  reasoning. Clean conversation, not a debug dump.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .personas import CHAT_PERSONA

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Slash command replies
# ════════════════════════════════════════════════════════════════════
#
# These are deliberately fixed strings — slash commands should be
# instantaneous and never burn an LLM call. The Russian-first phrasing
# matches the persona's voice.
#
_WELCOME = (
    "Привет. Я Аня — фрилансер-агент.\n"
    "Сейчас умею: редактировать английские DOCX, переводить с английского "
    "на русский, писать Python-скрипты и собирать презентации.\n"
    "Пришли файл или опиши задачу — разберусь. Просто хочешь поговорить — пиши, я отвечу."
)
_FIRST_TIME_PREFIX = (
    "Привет — это твоё первое сообщение, так что коротко: я Аня, фрилансер-агент. "
    "Редактирую английские DOCX, перевожу EN→RU, пишу Python-скрипты, собираю слайды. "
    "Команда /help — подробнее. А теперь к делу:"
)
_HELP = (
    "Что я умею прямо сейчас:\n"
    " - почистить английский DOCX-документ\n"
    " - перевести английский текст на русский\n"
    " - написать Python-скрипт по описанию\n"
    " - собрать черновик презентации\n"
    "\n"
    "Команды:\n"
    " /start — поздороваться\n"
    " /help — этот список\n"
    " /ping — проверить, что я на связи"
)
_PONG = "На связи."


# ════════════════════════════════════════════════════════════════════
# Reply
# ════════════════════════════════════════════════════════════════════

@dataclass
class ChatReply:
    """One outbound message ready to be sent over a chat channel."""

    text:           str
    needs_approval: bool = False
    used_workflow:  bool = False
    job_id:         str | None = None
    deliverables:   list[str] = field(default_factory=list)

    def short_repr(self) -> str:
        return (
            f"ChatReply(text={self.text[:60]!r}"
            f"{', workflow' if self.used_workflow else ''}"
            f"{', approval' if self.needs_approval else ''})"
        )


# ════════════════════════════════════════════════════════════════════
# Handler
# ════════════════════════════════════════════════════════════════════

# Words that signal "this is a freelance job, not a chat".
# The matcher is intentionally permissive — false positives still get a
# polite job-style reply; false negatives just fall through to chat.
_JOB_KEYWORDS = (
    "edit",       "edited",
    "translate",  "translation",
    "proofread",
    "rewrite",
    "summarize",  "summary",
    "правк",      "перевед", "перевод",
    "translation",
    "выправ",     "вычит",   "редакт",
    "напиши",     "написать",
    "слайды",     "презентац",
)


class ChatHandler:
    """Glue between a chat surface (Telegram) and the Brain.

    Single instance per process. Takes a callable for sending replies so
    the same handler works for Telegram, CLI, or test stubs.
    """

    def __init__(
        self,
        *,
        brain,
        job_store=None,
        attachments_dir=None,
        welcome_book=None,
    ) -> None:
        self._brain = brain
        self._job_store = job_store
        self._attachments_dir = attachments_dir
        self._welcome_book = welcome_book

    # ────────────────────────────────────────────────────────────────

    def handle(
        self,
        *,
        brief: str,
        client_id: str,
        attachments: list[str] | None = None,
        source: str = "chat",
    ) -> ChatReply:
        """Decide chat-vs-workflow and return one ready-to-send reply.

        Empty input returns a `ChatReply` with `text=""` — callers should
        check `.text` and skip the actual send. Silence is the right
        response to no input.
        """
        brief = (brief or "").strip()
        attachments = attachments or []

        if not brief and not attachments:
            return ChatReply(text="")          # honour silence

        cmd_reply = _slash_command(brief)
        if cmd_reply is not None:
            return ChatReply(text=cmd_reply)

        if attachments or _looks_like_job(brief):
            reply = self._handle_as_job(
                brief=brief or "(no brief)",
                client_id=client_id,
                attachments=attachments,
                source=source,
            )
        else:
            reply = self._handle_as_chat(brief=brief, client_id=client_id)

        # First-time clients get a small greeting in front of the actual
        # reply. We prepend rather than send-and-send so the user sees ONE
        # message, not two — feels less robotic.
        if self._is_first_contact(client_id) and reply.text:
            reply.text = f"{_FIRST_TIME_PREFIX}\n\n{reply.text}"
        return reply

    def _is_first_contact(self, client_id: str) -> bool:
        """True iff this client_id hasn't been greeted yet (then marks it)."""
        if self._welcome_book is None:
            return False
        # mark_greeted returns True only when the row was inserted now —
        # idempotent across restarts because the table is on disk.
        try:
            return self._welcome_book.mark_greeted(client_id)
        except Exception:  # noqa: BLE001 — welcome failures must not block chat
            logger.exception("[chat] welcome_book.mark_greeted raised")
            return False

    # ── conversational path ─────────────────────────────────────
    def _handle_as_chat(self, *, brief: str, client_id: str) -> ChatReply:
        # Empty briefs are filtered out by `handle()`; keep this for safety.
        if not brief:
            return ChatReply(text="")

        try:
            result = self._brain.think(
                brief,
                session_id=_session_for(client_id),
                system_prompt=CHAT_PERSONA,
            )
        except TypeError:
            # Older Brain stub in tests doesn't accept system_prompt kwarg.
            try:
                result = self._brain.think(brief, session_id=_session_for(client_id))
            except Exception:  # noqa: BLE001
                logger.exception("[chat] brain.think raised (fallback)")
                return ChatReply(text="Что-то задумалась слишком надолго. Попробуй ещё раз.")
        except Exception:  # noqa: BLE001 — never crash on chat
            logger.exception("[chat] brain.think raised")
            return ChatReply(text="Что-то задумалась слишком надолго. Попробуй ещё раз.")

        # We only ever ship plain text from `respond` / `clarify` results.
        # Anything else (tool_call, wait, stop) is internal state.
        action = getattr(result, "action", "")
        content = getattr(result, "content", "")

        if action in {"respond", "clarify"} and isinstance(content, str) and content.strip():
            return ChatReply(text=content.strip())

        if action == "wait":
            text = (
                _safe_str(content)
                or "Дай мне минутку — пока недостаточно ясно. Уточнишь?"
            )
            return ChatReply(text=text, needs_approval=getattr(result, "needs_human_approval", False))

        if action == "stop":
            return ChatReply(text="Остановилась. Напиши, когда захочешь продолжить.")

        # Unknown action — fall back to a safe acknowledgement.
        return ChatReply(text="Поняла тебя. Что дальше?")

    # ── workflow path ───────────────────────────────────────────
    def _handle_as_job(
        self,
        *,
        brief: str,
        client_id: str,
        attachments: list[str],
        source: str,
    ) -> ChatReply:
        from brain.skills.job import Job  # local — soft dep
        job = Job(brief=brief, source=source, client_id=client_id, attachments=attachments)
        if self._job_store is not None:
            try:
                self._job_store.create(job)
            except Exception:  # noqa: BLE001
                logger.exception("[chat] JobStore.create failed")
                return ChatReply(
                    text="Не удалось завести заказ — попробуй ещё раз чуть позже.",
                    used_workflow=True,
                )

        try:
            outcome = self._brain.intake_job(job)
        except Exception:  # noqa: BLE001
            logger.exception("[chat] brain.intake_job raised")
            return ChatReply(
                text="Что-то сломалось во время работы. Я записала ошибку и разберусь.",
                used_workflow=True, job_id=job.id,
            )

        text = _render_outcome(outcome)
        return ChatReply(
            text=text,
            used_workflow=True,
            job_id=outcome.job_id,
            deliverables=list(getattr(outcome, "deliverables", []) or []),
        )


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _slash_command(text: str) -> str | None:
    """Match `/start`, `/help`, `/ping` (with optional @botname suffix).

    Returns the canned reply or None if `text` isn't a known command. We
    intentionally do not call the LLM for these — Telegram convention says
    slash commands should respond instantly.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    head = stripped.split()[0].split("@", 1)[0].lower()
    if head == "/start":
        return _WELCOME
    if head == "/help":
        return _HELP
    if head == "/ping":
        return _PONG
    return None


def _looks_like_job(text: str) -> bool:
    """Heuristic only — channels with attachments override this anyway."""
    lowered = text.lower()
    if not lowered:
        return False
    return any(kw in lowered for kw in _JOB_KEYWORDS)


def _session_for(client_id: str) -> str:
    """Stable session id for chat continuity, sanitised for SQL keys."""
    safe = re.sub(r"[^A-Za-z0-9._:-]+", "_", client_id) or "anon"
    return f"chat:{safe}"


def _safe_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _render_outcome(outcome) -> str:
    """Turn a JobOutcome into a friendly chat message.

    We deliberately avoid technical fields (step ids, plan structure,
    raw verifier scores) — the user only needs a human-shaped summary.
    """
    from brain.skills.job import JobStatus
    status = getattr(outcome, "final_status", None)
    deliverables = list(getattr(outcome, "deliverables", []) or [])

    if status == JobStatus.DELIVERED:
        if deliverables:
            return (
                "Готово. Прикреплю результат отдельным сообщением, как только смогу."
            )
        return "Готово. Если нужно — пришлю детали."
    if status == JobStatus.FAILED:
        report = getattr(outcome, "verifier_report", None)
        reason = getattr(report, "summary", "") if report else ""
        if reason:
            return f"Не удалось довести заказ до конца: {reason}. Дай знать, что подправить."
        return "Не удалось завершить заказ — нужны уточнения."
    if status == JobStatus.DECLINED:
        return (
            "Этот заказ пока вне моих возможностей. "
            "Опиши задачу другими словами или пришли документ — попробую снова."
        )
    return "Записала. Я подумаю и вернусь."
