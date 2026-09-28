"""Jev (TypeSafe System One): вероятность, что новый вывод заменяет каждый из прежних похожих."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from core.redaction import redact_dlp_text

FLAG_ENV = "AGENT_JEV_MEMORY"
KEY_ENV = "TYPESAFE_API_KEY"
HOST = "api.typesafe.ai"
MODEL = "jev-latest"
THRESHOLD = 0.5
_ATTEMPTS = 3
_RETRY_STATUSES = frozenset({429, 529})
_TIMEOUT_SECONDS = 20.0
_TEXT_CHARS = 4000
_INSTRUCTIONS = "Is the fact in `older[{i}]` replaced, contradicted or withdrawn by `new`?"
_CRITERIA = {
    "true": "`new` gives a new value for the same fact, corrects it, or cancels the rule.",
    "false": ("`new` does not change this fact. A note about a different item, even a similar-sounding "
              "one, does not count; a note that states no fact is never replaced."),
}

Transport = Callable[[dict[str, Any], str], tuple[int, dict[str, Any]]]


class JevError(RuntimeError):
    pass


def jev_key() -> str | None:
    if (os.getenv(FLAG_ENV, "") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    return (os.getenv(KEY_ENV, "") or "").strip() or None


def http_transport(body: dict[str, Any], key: str) -> tuple[int, dict[str, Any]]:
    from tools.network_safety import NetworkSafetyPolicy, build_safe_opener

    request = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    policy = NetworkSafetyPolicy(tool_name="jev", egress_allow_hosts=(HOST,))
    policy.validate_url(request.full_url)
    try:
        with build_safe_opener(policy).open(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        err.close()
        return err.code, {}


def _clean(text: Any) -> str:
    return redact_dlp_text(str(text))[0][:_TEXT_CHARS]


def replacement_probabilities(
    key: str, new_text: str, candidates: Sequence[Any], *,
    transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, float]:
    """{id прежнего: p «новый его заменяет»}; 429/529 — повтор с паузой, прочий сбой — JevError."""
    if not candidates:
        return {}
    ids = {f"q{i}": record.id for i, record in enumerate(candidates)}
    body = {
        "state": {"new": _clean(new_text), "older": [_clean(record.content) for record in candidates]},
        "model": MODEL,
        "questions": {q: {"type": "noul", "instructions": _INSTRUCTIONS.format(i=i), "criteria": _CRITERIA}
                      for i, q in enumerate(ids)},
    }
    send = transport or http_transport
    status, data = 0, {}
    for attempt in range(_ATTEMPTS):
        status, data = send(body, key)
        if status not in _RETRY_STATUSES or attempt == _ATTEMPTS - 1:
            break
        sleep(2.0 ** attempt)
    if not 200 <= status < 300:
        raise JevError(f"Jev HTTP {status}")
    answers = data.get("answers") or {}
    try:
        return {record_id: float(answers[q]["noul"]) for q, record_id in ids.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise JevError(f"Jev answer malformed: {exc!r}") from None
