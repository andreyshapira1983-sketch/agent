"""Поток данных наружу: частное прочитанное не уходит по адресу из прочитанного.

Проверка ворот 25.09: `web_fetch` помечен «только чтение» и проходит всегда, а
запрос GET несёт данные в самом адресе (`…?addr=<адрес клиента>`). Словами
тихую подброшенную просьбу не поймать (замер guard_eval: наш страж и Prompt
Guard 2 на тихих атаках InjecAgent — 0% и 0–2,7%), поэтому проверяется поток:
откуда пришли части вызова. Идея — CaMeL (Debenedetti и др., «Defeating
Prompt Injections by Design», arXiv 2503.18813): у значения есть происхождение,
и политика смотрит на него при вызове инструмента.

Запрос наружу ждёт человека, только когда сошлось ОБА:

* адрес сервера взят из прочитанного (файл, страница), а не из просьбы
  человека и не из результатов поиска — подброшенный текст сам называет,
  куда слать;
* в пути или параметрах адреса едут частные данные этого хода (прочитанное
  из рабочей папки и памяти), которых нет в просьбе человека.

Обычный поиск по термину из файла (адрес — Википедия из поиска или известный
сайт) не задевается: сервер не из прочитанного. Переход по ссылке, целиком
написанной в прочитанном, — тоже: утечку отличает СОБРАННЫЙ адрес (сервер из
текста плюс вставленные частные данные), готовой такой строки нигде нет —
подбросивший не знает чужих данных заранее. Цена ложной тревоги — вопрос
человеку, а не отказ навсегда.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

#: Инструменты, отправляющие запрос по адресу из аргумента `url`.
EGRESS_TOOLS = frozenset({"web_fetch", "rss_fetch"})
#: Их вывод — частное: рабочая папка, журналы, память, прогоны кода.
PRIVATE_TOOLS = frozenset({
    "file_read", "find_in_files", "list_dir", "read_logs", "diff_file", "convert_file",
    "memory_recall", "python_probe", "run_tests", "lesson_provenance", "model_roster",
})
#: Их вывод выбирает сервер законно: адреса из выдачи поиска.
SEARCH_TOOLS = frozenset({"web_search", "semantic_scholar_search"})

_TOKEN = re.compile(r"[\w@.+-]{6,}")
_HOST = re.compile(r"(?<![\w.-])((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\w-])", re.IGNORECASE)
_CAP = 2_000_000


class EgressLedger:
    """Что просил человек и что прочитано в этом ходе — для ворот."""

    def __init__(self, user_text: str = "") -> None:
        self.user = (user_text or "").lower()
        self.private: list[str] = []
        self.read: list[str] = []
        self.read_hosts: set[str] = set()
        self.search_hosts: set[str] = set()
        self._size = 0

    def note(self, tool_name: str, output: object) -> None:
        text = str(output or "")
        if not text:
            return
        hosts = {h.lower() for h in _HOST.findall(text)}
        if tool_name in SEARCH_TOOLS:
            self.search_hosts |= hosts
            return
        self.read_hosts |= hosts
        if self._size >= _CAP:
            return
        low = text.lower()[: _CAP - self._size]
        self._size += len(low)
        self.read.append(low)
        if tool_name in PRIVATE_TOOLS:
            self.private.append(low)

    def leak(self, tool_name: str, arguments: dict) -> str | None:
        """Почему запрос ждёт человека, или None — пропустить."""
        if tool_name not in EGRESS_TOOLS:
            return None
        parts = urlsplit(str((arguments or {}).get("url") or ""))
        host = (parts.hostname or "").lower()
        if not host or host in self.user or host in self.search_hosts or host not in self.read_hosts:
            return None
        link = unquote(f"{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")).lower()
        if any(link in text for text in self.read):
            return None  # переход по готовой ссылке из прочитанного, а не собранный адрес
        carried = unquote(f"{parts.path} {parts.query} {parts.fragment}").lower()
        for token in _TOKEN.findall(carried):
            if token in host or token in self.user:
                continue
            if any(token in text for text in self.private):
                return (f"the URL host {host} comes from content read this turn, not from the request, "
                        f"and the URL carries private data read this turn ({token[:24]!r}) — "
                        "sending it out needs a human")
        return None
