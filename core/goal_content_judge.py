"""Судья цели читает ПРОДУКТ, а не только находит его на диске.

Сверка 2026-09-25 (артефакт «Как учат агентов» против кода): судья цели
(core/campaign_verdict.py) говорит «verified», если названные критерием файлы
есть и свежие. Критерий свободным текстом — «документ называет каждое
хранилище с его писателями» — сводился к «файл появился», и «проверено» у
учёбы и задач становилось бумажкой: из 873 целей с «результатом» судья
признал 168, и все по наличию файла (мерило полезности, тот же день).

Модель сюда не звали намеренно: судить слово модели той же моделью. Решение
по литературе — судья с СОБСТВЕННЫМ доступом к продукту и проверкой по
требованиям:

* Agent-as-a-Judge (Zhuge et al., ICML 2025, arXiv 2410.10934): судья сам
  открывает артефакты и проверяет каждое требование — ~90 % совпадения с
  людьми против 60–70 % у «модель оценивает ответ»;
* TICK (Cook et al., arXiv 2410.03608): критерий раскладывается на вопросы
  «да/нет», по одному на требование, — согласие с людьми выше общей оценки.

Защита от доверия на слово — без модели: каждое «да» обязано нести дословную
цитату, и код ищет её в самом файле. «Да» без найденной цитаты — «нет».
Судья зовётся только когда файлы уже найдены и свежие: по пути вызовов нет.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: Сколько текста продукта видит судья: по файлу и всего.
_PER_FILE_CHARS = 12_000
_TOTAL_CHARS = 24_000
_MAX_QUESTIONS = 5
#: Цитата короче — не доказательство (совпадёт случайно).
_MIN_QUOTE = 12

_SYSTEM = (
    "You check whether a finished piece of work meets its success criterion. You get the CRITERION "
    "and the FILE CONTENT the work produced. Split the criterion into 1-5 concrete yes/no requirement "
    "questions (one per requirement it states) about WHAT THE FILE SAYS - that the file exists is "
    "already checked, do not ask it. Answer each STRICTLY from the file content. For every "
    "'yes' give a verbatim quote (12-200 characters) copied exactly from the file that shows it; if you "
    "cannot quote it, the answer is 'no'. Reply with JSON only: "
    '[{"question": "...", "answer": "yes" | "no", "quote": "..."}]'
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _read(workspace: Any, paths: list[str]) -> list[tuple[str, str]]:
    """(путь, видимый судье кусок) по каждому файлу продукта."""
    parts, total = [], 0
    for rel in paths:
        try:
            text = (Path(workspace or ".") / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        room = min(_PER_FILE_CHARS, _TOTAL_CHARS - total)
        # Журнал дописывается в конец: свежий продукт цели — последние строки.
        # Живая проба 2026-09-25: заявка в data/causal_claims.jsonl лежала за
        # первыми 12 000 знаков, и судья честно её «не нашёл».
        chunk = text[-room:] if rel.endswith((".jsonl", ".log")) and room > 0 else text[:room]
        if not chunk:
            break
        parts.append((rel, chunk))
        total += len(chunk)
    return parts


def _parse(raw: str) -> list[dict[str, str]] | None:
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        rows = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out = [{"question": str(r.get("question") or "")[:200], "answer": str(r.get("answer") or "").lower(),
            "quote": str(r.get("quote") or "")} for r in rows if isinstance(r, dict)]
    return out[:_MAX_QUESTIONS] or None


def judge_content(success_check: str, fresh_paths: list[str], workspace: Any, llm: Any) -> dict[str, Any] | None:
    """Проверка содержания продукта по критерию, или None (судить нечем).

    Возвращает {"met": bool, "unmet": [вопросы], "checked": n}. None — нет
    модели, нет текста продукта или ответ судьи не разобрать: тогда вердикт
    остаётся прежним, файловым, — незнание не выдаётся за «нет».
    """
    if llm is None or not fresh_paths:
        return None
    parts = _read(workspace, fresh_paths)
    if not parts:
        return None
    content = "\n\n".join(f"=== FILE {rel} ===\n{chunk}" for rel, chunk in parts)
    try:
        raw = llm.complete(system=_SYSTEM, user=f"CRITERION:\n{success_check}\n\nFILE CONTENT:\n{content}",
                           max_tokens=900)
    except Exception:  # noqa: BLE001 — судья содержания не валит вердикт
        return None
    rows = _parse(str(getattr(raw, "text", raw)))
    if rows is None:
        return None
    # Цитата ищется в текстах файлов, не в заголовках «=== FILE … ===»: живая
    # проба 2026-09-25 — на «файл создан?» судья процитировал заголовок.
    haystack = _norm("\n".join(chunk for _, chunk in parts))
    unmet = []
    for row in rows:
        quote = _norm(row["quote"])
        backed = row["answer"] == "yes" and len(quote) >= _MIN_QUOTE and quote in haystack
        if not backed:
            unmet.append(row["question"] or "(unnamed requirement)")
    return {"met": not unmet, "unmet": unmet, "checked": len(rows)}
