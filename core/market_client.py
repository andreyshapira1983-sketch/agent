"""Клиент Agent Market (NEAR AI, market.near.ai) для агента-исполнителя, режим self-hosted + опрос.

Задача оператора 2026-09-25: подключить агента опросом (без webhook), журнал
каждого запроса и ответа, ошибки, пределы частоты и повторы; токен — только
в переменной окружения. Контракт — первоисточники площадки, прочитанные до
правки: /skill, /skill/flows/worker-lifecycle.md, /skill/reference/agent-api.md,
/openapi.json.

Что берём из них:
* авторизация `Authorization: Bearer aat_…`; токен знает своего агента сам;
* 5xx и сетевые сбои — повтор с нарастающей паузой; 429 — по `Retry-After`;
  4xx — не повторять (их надо чинить, повтор не поможет);
* `start` и повторная сдача тем же URL и хешем безопасно повторяемы.

Чего здесь НАРОЧНО нет: ставок (`POST /v1/jobs/{id}/bids`) и всего, что
тратит или публикует. До слова оператора клиент их не умеет вовсе — запрет
держится устройством, а не памятью исполнителя.
"""
from __future__ import annotations

import json
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOKEN_ENV = "AGENT_MARKET_API_TOKEN"  # noqa: S105 — имя переменной, не сам токен
BASE_ENV = "AGENT_MARKET_BASE_URL"
DEFAULT_BASE = "https://market.near.ai"
#: Всё, что похоже на токен агента, вычищается из журнала, где бы ни встретилось.
_TOKEN_RE = re.compile(r"aat_[0-9a-fA-F]{8,}")
#: Подписанный адрес — сам себе пропуск (directUploadUrl?sig=…): строка запроса
#: у любого адреса в журнале вычищается. Нашёл тест 2026-09-25.
_URL_QUERY_RE = re.compile(r"(https?://[^\s\"\\?]+)\?[^\s\"\\]*")
_BODY_LOG_CHARS = 2000


class MarketError(Exception):
    """Ответ площадки 4xx/5xx после всех повторов: код, машинная метка, текст."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


def _redact(text: str) -> str:
    return _URL_QUERY_RE.sub(r"\1?<redacted>", _TOKEN_RE.sub("aat_<redacted>", text))


def _tls_context() -> ssl.SSLContext:
    """Проверка сертификата всегда включена; certifi — потому что хранилище
    Windows не знает цепочку Let's Encrypt YR2, которой подписан market.near.ai
    (замер 2026-09-25: «unable to get issuer certificate»)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover — без certifi берём системное хранилище
        return ssl.create_default_context()


class MarketClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        log_path: Path | str | None = None,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self._token = token if token is not None else os.environ.get(TOKEN_ENV, "")
        if not self._token:
            raise MarketError(0, "no_token", f"переменная {TOKEN_ENV} не задана")
        self.base_url = (base_url or os.environ.get(BASE_ENV) or DEFAULT_BASE).rstrip("/")
        local = self.base_url.startswith(("http://127.0.0.1", "http://localhost"))
        if not (self.base_url.startswith("https://") or local):
            raise MarketError(0, "insecure_base", "площадка — только https (http лишь для локальной проверки)")
        self.log_path = Path(log_path) if log_path else None
        self.max_attempts, self._sleep, self.timeout = max_attempts, sleep, timeout
        self._ctx = _tls_context()

    # ── транспорт ────────────────────────────────────────────────────────────
    def log_event(self, row: dict[str, Any]) -> None:
        """Событие исполнителя в тот же журнал, с той же чисткой токена."""
        self._log({"ts": datetime.now(timezone.utc).isoformat(), **row})

    def _log(self, row: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(_redact(json.dumps(row, ensure_ascii=False)) + "\n")

    def _send(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, dict, bytes]:
        if not headers:
            return self._send_bare(method, url, body or b"")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 — схема проверена в __init__: https или локальный http
        ctx = self._ctx if url.startswith("https://") else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:  # noqa: S310 — см. выше
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers or {}), err.read() or b""

    def _send_bare(self, method: str, url: str, body: bytes) -> tuple[int, dict, bytes]:
        """PUT на подписанный адрес: «ровно byteSize байт, без лишних заголовков»
        (worker-lifecycle §4). urllib сам добавил бы Content-Type и мог бы
        сломать подпись, поэтому — голый http.client: Host и Content-Length."""
        import http.client
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.netloc, timeout=self.timeout, context=self._ctx)
        else:
            conn = http.client.HTTPConnection(parts.netloc, timeout=self.timeout)
        try:
            conn.putrequest(method, parts.path + (f"?{parts.query}" if parts.query else ""),
                            skip_accept_encoding=True)
            conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 300.0)
            except ValueError:
                pass
        return min(60.0, 2.0 ** attempt) + random.uniform(0, 1)  # noqa: S311 — дрожание паузы, не криптография

    def request(self, method: str, path: str, payload: Any = None, *, auth: bool = True,
                raw_body: bytes | None = None, url: str | None = None) -> Any:
        """Запрос с журналом и повторами. Возвращает разобранный JSON (или None)."""
        target = url or f"{self.base_url}{path}"
        # Чужой подписанный адрес (загрузка файла) — без единого своего заголовка.
        headers: dict[str, str] = {} if url is not None else {"Accept": "application/json"}
        body = raw_body
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                status, resp_headers, data = self._send(method, target, body, headers)
                error = None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                status, resp_headers, data, error = 0, {}, b"", f"{type(exc).__name__}: {exc}"
            text = data.decode("utf-8", "replace")
            self._log({
                "ts": datetime.now(timezone.utc).isoformat(), "method": method,
                # Подписанный адрес загрузки — сам себе пропуск: в журнал идёт без запроса.
                "path": path if url is None else target.split("?", 1)[0],
                "attempt": attempt, "status": status, "ms": int((time.monotonic() - started) * 1000),
                "request": payload if payload is not None else (f"<{len(body)} bytes>" if body else None),
                "response": text[:_BODY_LOG_CHARS], "error": error,
            })
            retryable = status in {0, 429} or status >= 500
            if 200 <= status < 300:
                return json.loads(text) if text.strip() else None
            if not retryable or attempt == self.max_attempts:
                break
            self._sleep(self._backoff(attempt, resp_headers.get("Retry-After") or resp_headers.get("retry-after")))
        code, message = "network_error" if status == 0 else "http_error", error or text[:300]
        try:
            parsed = json.loads(text)
            code, message = parsed.get("error", code), parsed.get("message", message)
        except (ValueError, AttributeError):
            pass
        raise MarketError(status, str(code), str(message))

    # ── методы исполнителя (без ставок — см. докстринг модуля) ───────────────
    def my_assignments(self, status: str = "in_progress") -> list[dict]:
        return (self.request("GET", f"/v1/agents/me/assignments?status={status}") or {}).get("assignments", [])

    def my_bids(self, status: str = "all") -> list[dict]:
        return (self.request("GET", f"/v1/agents/me/bids?status={status}") or {}).get("bids", [])

    def start(self, assignment_id: str) -> dict:
        return self.request("POST", f"/v1/assignments/{assignment_id}/start")

    def messages(self, assignment_id: str) -> Any:
        return self.request("GET", f"/v1/assignments/{assignment_id}/messages")

    def upload_file(self, filename: str, data: bytes) -> str:
        """reserve → PUT (без токена: адрес подписан сам) → complete; вернуть fileUrl."""
        slot = self.request("POST", "/v1/files", {"filename": filename, "byteSize": len(data), "link": True})
        self.request("PUT", "<directUploadUrl>", auth=False, raw_body=data, url=slot["directUploadUrl"])
        self.request("POST", f"/v1/files/{slot['fileId']}/complete")
        return slot.get("fileUrl") or slot["uploadUrl"]

    def submit(self, assignment_id: str, deliverable_url: str, deliverable_hash: str | None = None) -> dict:
        payload = {"deliverableUrl": deliverable_url}
        if deliverable_hash:
            payload["deliverableHash"] = deliverable_hash
        return self.request("POST", f"/v1/assignments/{assignment_id}/submit", payload)

    def find_assignment(self, assignment_id: str) -> dict | None:
        for row in self.my_assignments("all"):
            if (row.get("assignment") or {}).get("assignmentId") == assignment_id:
                return row
        return None
