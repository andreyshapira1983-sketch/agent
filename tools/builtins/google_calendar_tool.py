"""
tools/builtins/google_calendar_tool.py — Google Calendar Integration

Позволяет агенту читать, создавать и удалять события в Google Calendar.

Требования:
    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2

Конфигурация (.env):
    GOOGLE_CREDENTIALS_PATH = config/credentials.json   # OAuth client JSON из Google Cloud

При первом запуске откроется браузер для авторизации.
После авторизации токен сохраняется в config/token.json автоматически.

Действия (action):
    list    — список событий (параметры: days_ahead=7, max_results=10)
    create  — создать событие (title, start, end, description="")
    delete  — удалить событие (event_id)
    get     — получить одно событие (event_id)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.base import ToolBase, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = Path("config/token.json")
CREDS_PATH = Path(os.environ.get("GOOGLE_CREDENTIALS_PATH", "config/credentials.json"))


class GoogleCalendarTool(ToolBase):
    """
    Управляет событиями в Google Calendar через OAuth 2.0.
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="google_calendar",
            description=(
                "Управляет Google Calendar: список событий, создание, удаление. "
                "action='list' — список на N дней вперёд. "
                "action='create' — создать событие. "
                "action='delete' — удалить по event_id."
            ),
            parameters={
                "action":      "str: 'list' | 'create' | 'delete' | 'get'",
                "days_ahead":  "int: для list — сколько дней вперёд (default 7)",
                "max_results": "int: для list — максимум событий (default 10)",
                "title":       "str: для create — название события",
                "start":       "str: для create — начало 'YYYY-MM-DD HH:MM' или 'YYYY-MM-DD'",
                "end":         "str: для create — конец 'YYYY-MM-DD HH:MM' или 'YYYY-MM-DD'",
                "description": "str: для create — описание (необязательно)",
                "event_id":    "str: для delete/get — ID события",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "list")).lower()
        try:
            service = self._get_service()
            if action == "list":
                return self._list_events(service, params)
            elif action == "create":
                return self._create_event(service, params)
            elif action == "delete":
                return self._delete_event(service, params)
            elif action == "get":
                return self._get_event(service, params)
            else:
                return ToolResult(
                    tool_name="google_calendar",
                    success=False,
                    output=None,
                    error=f"Неизвестное действие: '{action}'. Допустимо: list, create, delete, get",
                )
        except Exception as exc:
            logger.exception("[GoogleCalendarTool] Ошибка: %s", exc)
            return ToolResult(
                tool_name="google_calendar",
                success=False,
                output=None,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list_events(self, service: Any, params: dict) -> ToolResult:
        days_ahead = int(params.get("days_ahead", 7))
        max_results = int(params.get("max_results", 10))

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = result.get("items", [])
        formatted = []
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", "?"))
            formatted.append({
                "id":          ev["id"],
                "title":       ev.get("summary", "(без названия)"),
                "start":       start,
                "end":         ev["end"].get("dateTime", ev["end"].get("date", "?")),
                "description": ev.get("description", ""),
                "link":        ev.get("htmlLink", ""),
            })

        text_lines = [f"Событий на {days_ahead} дней вперёд: {len(formatted)}"]
        for ev in formatted:
            text_lines.append(f"  • {ev['start'][:16]}  {ev['title']}  [id={ev['id'][:12]}...]")

        return ToolResult(
            tool_name="google_calendar",
            success=True,
            output="\n".join(text_lines),
            metadata={"events": formatted, "count": len(formatted)},
        )

    def _create_event(self, service: Any, params: dict) -> ToolResult:
        title = params.get("title")
        start_str = params.get("start")
        end_str = params.get("end")
        description = params.get("description", "")

        if not title or not start_str or not end_str:
            return ToolResult(
                tool_name="google_calendar",
                success=False,
                output=None,
                error="Для create обязательны: title, start, end",
            )

        start_obj = self._parse_dt(start_str)
        end_obj = self._parse_dt(end_str)

        body = {
            "summary":     title,
            "description": description,
            "start":       start_obj,
            "end":         end_obj,
        }

        created = service.events().insert(calendarId="primary", body=body).execute()

        return ToolResult(
            tool_name="google_calendar",
            success=True,
            output=f"Событие создано: '{title}' | id={created['id']} | ссылка: {created.get('htmlLink','')}",
            metadata={"event_id": created["id"], "link": created.get("htmlLink", "")},
        )

    def _delete_event(self, service: Any, params: dict) -> ToolResult:
        event_id = params.get("event_id")
        if not event_id:
            return ToolResult(
                tool_name="google_calendar",
                success=False,
                output=None,
                error="Для delete обязателен event_id",
            )
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return ToolResult(
            tool_name="google_calendar",
            success=True,
            output=f"Событие {event_id} удалено",
        )

    def _get_event(self, service: Any, params: dict) -> ToolResult:
        event_id = params.get("event_id")
        if not event_id:
            return ToolResult(
                tool_name="google_calendar",
                success=False,
                output=None,
                error="Для get обязателен event_id",
            )
        ev = service.events().get(calendarId="primary", eventId=event_id).execute()
        start = ev["start"].get("dateTime", ev["start"].get("date", "?"))
        return ToolResult(
            tool_name="google_calendar",
            success=True,
            output=f"{ev.get('summary','?')} | {start} | {ev.get('description','')}",
            metadata={"event": ev},
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_service(self) -> Any:
        """Возвращает авторизованный Google Calendar service."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None

        # Загружаем сохранённый токен
        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # Если токен истёк — обновляем
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_token(creds)

        # Если нет токена или невалидный — OAuth flow (открывает браузер)
        if not creds or not creds.valid:
            if not CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"Файл credentials.json не найден: {CREDS_PATH}\n"
                    "Скачай его из Google Cloud Console → APIs & Services → Credentials"
                )

            # Определяем тип credentials (installed или web)
            creds_data = json.loads(CREDS_PATH.read_text())
            client_type = "installed" if "installed" in creds_data else "web"

            if client_type == "installed":
                flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                # web тип — тоже работает через local server
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDS_PATH),
                    SCOPES,
                    redirect_uri="http://localhost:8080/",
                )
                creds = flow.run_local_server(port=8080)

            self._save_token(creds)

        return build("calendar", "v3", credentials=creds)

    def _save_token(self, creds: Any) -> None:
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())
        logger.debug("[GoogleCalendarTool] Токен сохранён: %s", TOKEN_PATH)

    @staticmethod
    def _parse_dt(dt_str: str) -> dict:
        """Парсит 'YYYY-MM-DD HH:MM' или 'YYYY-MM-DD' в формат Google API."""
        dt_str = dt_str.strip()
        if " " in dt_str or "T" in dt_str:
            # datetime с временем
            dt_str = dt_str.replace(" ", "T")
            if len(dt_str) == 16:
                dt_str += ":00"
            return {"dateTime": dt_str + "+03:00", "timeZone": "Europe/Moscow"}
        else:
            # только дата — всё-дневное событие
            return {"date": dt_str}
