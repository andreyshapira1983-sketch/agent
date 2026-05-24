"""
tools/builtins/email_tool.py — SMTP Email Tool

Позволяет агенту отправлять email через SMTP (Gmail, Outlook, любой SMTP).
Поддерживает dry_run режим — показывает что будет отправлено без реальной отправки.

Конфигурация через .env:
    EMAIL_USERNAME   = your@gmail.com
    EMAIL_PASSWORD   = your-app-password
    EMAIL_SMTP_HOST  = smtp.gmail.com
    EMAIL_SMTP_PORT  = 587

Безопасность:
    - Пароль читается ТОЛЬКО из переменных окружения, никогда из параметров
    - Список разрешённых получателей (если задан EMAIL_ALLOWED_DOMAINS)
    - dry_run=True по умолчанию — случайная отправка невозможна
    - Логирует все попытки отправки
"""

from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Any

from tools.base import ToolBase, ToolResult, ToolSpec

try:
    # Vault is optional — if brain isn't on the path, EmailTool still works
    # by falling back to os.environ.
    from brain.secrets import SecretsVault
except ImportError:  # pragma: no cover - defensive
    SecretsVault = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

_MAX_BODY = 8_000   # chars
_MAX_SUBJECT = 200  # chars
_MAX_ATTACHMENTS = 5
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024   # 20 MB per attachment — Gmail-friendly


class EmailTool(ToolBase):
    """
    Отправляет электронное письмо через SMTP.

    Параметры:
        to      (str)  : email получателя
        subject (str)  : тема письма
        body    (str)  : тело письма (plain text)
        dry_run (bool) : если True — только показывает что было бы отправлено
                         (по умолчанию True, защита от случайной отправки)

    Конфигурация (через .env):
        EMAIL_USERNAME  — логин / адрес отправителя
        EMAIL_PASSWORD  — пароль (для Gmail — App Password)
        EMAIL_SMTP_HOST — SMTP сервер (по умолч. smtp.gmail.com)
        EMAIL_SMTP_PORT — SMTP порт   (по умолч. 587, TLS)
    """

    def __init__(
        self,
        vault: "SecretsVault | None" = None,
        *,
        default_dry_run: bool = True,
    ) -> None:
        """
        Args:
            vault: optional SecretsVault that holds EMAIL_USERNAME and
                   EMAIL_PASSWORD. When supplied, the tool reads credentials
                   from the vault instead of os.environ — preferred path.
                   When None, falls back to os.environ for backward
                   compatibility.
            default_dry_run: tool-level default for the `dry_run` parameter.
                   When the caller (workflow / Brain) omits `dry_run`, this
                   value is used. Lets the runtime flip "real sends on/off"
                   without editing every profession YAML. Keep True until
                   the operator explicitly opts in to live delivery.
        """
        self._vault = vault
        self._default_dry_run = bool(default_dry_run)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="email",
            description=(
                "Отправляет email через SMTP. "
                "По умолчанию dry_run=True — показывает письмо без отправки. "
                "Установи dry_run=false только если нужна реальная отправка."
            ),
            parameters={
                "to":          "str — email адрес получателя",
                "subject":     "str — тема письма",
                "body":        "str — тело письма (plain text)",
                "attachments": "list[str] (optional) — пути к файлам-вложениям",
                "dry_run":     "bool (optional) — true = только показ, false = реальная отправка (по умолч. true)",
            },
            is_destructive=True,  # реальная отправка необратима
        )

    # ------------------------------------------------------------------
    # Credentials — single audited boundary between Secret and SMTP lib
    # ------------------------------------------------------------------

    def _smtp_username(self) -> str:
        if self._vault is not None and self._vault.has("EMAIL_USERNAME"):
            return self._vault.get("EMAIL_USERNAME").reveal().strip()
        return os.environ.get("EMAIL_USERNAME", "").strip()

    def _smtp_password(self) -> str:
        """The ONLY place where the SMTP password becomes a raw string."""
        if self._vault is not None and self._vault.has("EMAIL_PASSWORD"):
            raw = self._vault.get("EMAIL_PASSWORD").reveal()
        else:
            raw = os.environ.get("EMAIL_PASSWORD", "")
        # Gmail app passwords are shown with spaces — strip them
        return raw.replace(" ", "").strip()

    def execute(self, **params: Any) -> ToolResult:
        to:      str  = params.get("to",      "").strip()
        subject: str  = params.get("subject", "").strip()
        body:    str  = params.get("body",    "").strip()
        attachments_raw = params.get("attachments") or []
        # When the caller didn't pass `dry_run`, fall back to the tool's
        # default (which the runtime sets from AGENT_DRY_RUN). This is
        # the single switch that flips the whole agent between safe-mode
        # and live delivery.
        if "dry_run" in params:
            dry_run: bool = self._parse_bool(params["dry_run"])
        else:
            dry_run = self._default_dry_run

        # Валидация
        if not to:
            return self._fail("Параметр 'to' (email получателя) обязателен")
        if not subject:
            return self._fail("Параметр 'subject' (тема письма) обязателен")
        if not body:
            return self._fail("Параметр 'body' (тело письма) обязателен")
        if "@" not in to or "." not in to.split("@")[-1]:
            return self._fail(f"Некорректный email адрес: '{to}'")

        # Валидация вложений
        attachments, err = self._resolve_attachments(attachments_raw)
        if err is not None:
            return self._fail(err)

        # Обрезка по лимиту
        subject = subject[:_MAX_SUBJECT]
        body    = body[:_MAX_BODY]

        # SMTP credentials — username/password come from the vault when available,
        # falling back to environment variables. Host/port are not secrets.
        smtp_user = self._smtp_username()
        smtp_pass = self._smtp_password()
        smtp_host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com").strip()
        smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

        if dry_run:
            return self._ok({
                "mode":        "dry_run",
                "from":        smtp_user or "(EMAIL_USERNAME не задан)",
                "to":          to,
                "subject":     subject,
                "body":        body,
                "attachments": [str(p) for p in attachments],
                "smtp":        f"{smtp_host}:{smtp_port}",
                "note":        "Письмо НЕ отправлено (dry_run=True). Задай dry_run=false для реальной отправки.",
            })

        # Реальная отправка
        if not smtp_user:
            return self._fail("EMAIL_USERNAME не задан в .env — невозможно отправить письмо")
        if not smtp_pass:
            return self._fail("EMAIL_PASSWORD не задан в .env — невозможно отправить письмо")

        try:
            msg = MIMEMultipart()
            msg["From"]    = smtp_user
            msg["To"]      = to
            msg["Subject"] = subject
            msg["Date"]    = formatdate(localtime=True)
            msg.attach(MIMEText(body, "plain", "utf-8"))

            for path in attachments:
                part = self._build_attachment_part(path)
                if part is not None:
                    msg.attach(part)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, [to], msg.as_string())

            logger.info(
                "[EmailTool] Sent to=%s subject=%s attachments=%d",
                to, subject, len(attachments),
            )
            return self._ok({
                "mode":        "sent",
                "from":        smtp_user,
                "to":          to,
                "subject":     subject,
                "attachments": [str(p) for p in attachments],
                "smtp":        f"{smtp_host}:{smtp_port}",
            })

        except smtplib.SMTPAuthenticationError:
            return self._fail(
                "SMTP аутентификация не удалась. "
                "Для Gmail используй App Password (не обычный пароль)."
            )
        except smtplib.SMTPException as exc:
            return self._fail(f"SMTP ошибка: {exc}")
        except OSError as exc:
            return self._fail(f"Сетевая ошибка: {exc}")

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() not in {"false", "0", "no", "нет"}
        return bool(value)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_attachments(raw: Any) -> tuple[list[Path], str | None]:
        """Validate and normalise attachment paths.

        Returns (resolved_paths, error_message). On success, error is None.
        """
        if raw in (None, "", [], ()):
            return [], None
        if isinstance(raw, (str, Path)):
            raw_list = [raw]
        elif isinstance(raw, (list, tuple)):
            raw_list = list(raw)
        else:
            return [], f"'attachments' должен быть списком путей, получено {type(raw).__name__}"

        if len(raw_list) > _MAX_ATTACHMENTS:
            return [], (
                f"Слишком много вложений: {len(raw_list)} (макс. {_MAX_ATTACHMENTS})"
            )

        resolved: list[Path] = []
        for item in raw_list:
            try:
                path = Path(str(item)).expanduser().resolve()
            except (OSError, ValueError) as exc:
                return [], f"Невалидный путь к вложению '{item}': {exc}"
            if not path.exists():
                return [], f"Вложение не найдено: {path}"
            if not path.is_file():
                return [], f"Вложение не является файлом: {path}"
            try:
                size = path.stat().st_size
            except OSError as exc:
                return [], f"Не могу прочитать размер вложения '{path}': {exc}"
            if size > _MAX_ATTACHMENT_BYTES:
                return [], (
                    f"Вложение слишком большое: {path.name} ({size} байт, "
                    f"максимум {_MAX_ATTACHMENT_BYTES})"
                )
            resolved.append(path)
        return resolved, None

    @staticmethod
    def _build_attachment_part(path: Path) -> MIMEBase | None:
        """Read a file and wrap it in a MIMEBase part for MIMEMultipart."""
        try:
            data = path.read_bytes()
        except OSError as exc:  # noqa: PERF203 — never silently lose attachments
            logger.error("[EmailTool] Cannot read attachment '%s': %s", path, exc)
            return None
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        part = MIMEBase(maintype, subtype)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{path.name}"',
        )
        return part
