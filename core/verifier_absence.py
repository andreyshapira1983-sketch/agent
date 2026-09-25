"""Certifying a claim of ABSENCE — the half of verification a citation cannot do."""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence

#: Отличительные литералы: идентификаторы, SHA, пути, версионные триплеты (`3.11.9`).
#: Голые и десятичные числа не входят — их судит `evaluate_claim_arithmetic`.
#: Через дефис — только с цифрой внутри, иначе проза («read-only») стала бы идентификатором.
#: Посессивные кванторы и потолки {,160} держат разбор линейным на враждебном тексте.
_SALIENT_LITERAL_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]{0,160}+(?:_[A-Za-z0-9]{1,160}+)++"
    r"|[A-Za-z][A-Za-z0-9]{0,160}+"
    r"(?:-[A-Za-z0-9]{0,160}\d[A-Za-z0-9]{0,160}+)++(?:-[A-Za-z0-9]{1,160}+)*+"
    r"|[0-9a-f]{7,40}"
    r"|\d+(?:\.\d+){2,}"
    r"|[\w./\-]{1,160}\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml))\b"
)


#: Маркер ссылки в тексте ответа: `[file:core/loop.py]`, `[memory:mem_…]`.
_CITATION_TOKEN_RE = re.compile(r"\[[^\[\]]*\]")


def salient_literals(text: str) -> set[str]:
    """Отличительные литералы текста, в нижнем регистре."""
    return {m.group(0).lower() for m in _SALIENT_LITERAL_RE.finditer(text or "")}


def literals_absent_from_excerpt(
    claim: str, excerpt: str, source_id: str = ""
) -> set[str]:
    """Литералы утверждения, которых НЕТ в цитируемой улике."""
    if not claim or not excerpt:
        return set()
    # Маркер ссылки — адрес источника, а не утверждение о мире.
    body = _CITATION_TOKEN_RE.sub(" ", claim)
    # Имя собственного источника чужим литералом не считается.
    known = salient_literals(excerpt) | salient_literals(source_id or "")
    return {
        lit for lit in salient_literals(body)
        if lit not in known and not any(lit in k or k in lit for k in known)
    }


def absent_literal_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если утверждение называет то, чего нет в улике."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    # Утверждение об отсутствии судит пятый гейт по обратному правилу.
    if asserts_absence(chunk_text):
        return None
    # Усечённая улика не доказывает отсутствия: литерал мог быть в отрезанной части (R8).
    excerpt_raw = ev.excerpt or ""
    if ("[INTENT-BUDGET:" in excerpt_raw or "[TOTAL-BUDGET:" in excerpt_raw
            or excerpt_raw.rstrip().endswith("...[truncated]")):  # R8b: срез при создании, evidence.py:_truncate
        return None
    absent = literals_absent_from_excerpt(
        chunk_text, ev.excerpt or "", ev.source_id or ""
    )
    if not absent:
        return None
    from .verifier_models import ClaimReason
    # Путь, отличный от цитируемого источника, — приписывание (ложь о происхождении);
    # иначе лишь нехватка подпорки: вырезка может быть про другое место файла.
    cited = (ev.source_id or "").split(":", 1)[-1].replace("\\", "/").lower()
    foreign = [
        lit for lit in absent
        if "/" in lit and "." in lit.rsplit("/", 1)[-1]
        and lit.replace("\\", "/").lower() not in cited
    ]
    # Число, которого улика не печатала, — тоже ложь, а не нехватка подпорки.
    if not foreign:
        foreign = [lit for lit in absent if any(ch.isdigit() for ch in lit)]
    return ClaimReason(
        code="cited_literal_absent" if foreign else "cited_support_missing",
        expected=", ".join(sorted(absent)[:3]),
        actual="",
        explanation="утверждение называет то, чего нет в цитируемой улике",
        computed_from=ev.source_id or "",
    )


#: Минимум содержательных слов, чтобы расхождение с уликой что-то значило («Да.» — не предмет).
_OFF_TOPIC_MIN_TOKENS = 3


def _dominant_script(text: str) -> str:
    """`cyrillic`, `latin` или `none` — какой алфавит преобладает в тексте."""
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    lat = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    if cyr == 0 and lat == 0:
        return "none"
    if cyr > lat:
        return "cyrillic"
    if lat > cyr:
        return "latin"
    return "none"


def _subject_tokens(text: str) -> set[str]:
    """Содержательные слова без голых чисел: общая цифра не делает общей темы."""
    from .topic_tokens import discriminating_tokens

    # `discriminating_tokens`: только она применяет стоп-лист («the», «was»).
    return {t for t in discriminating_tokens(text) if not t.isdigit()}


#: Слова мета-утверждений — про сами улики, а не про мир («both files agree»).
_META_VOCABULARY: frozenset[str] = frozenset({
    "file", "files", "value", "values", "sum", "sums", "total", "count",
    "contains", "contain", "agree", "agrees", "report", "reports", "test",
    "tests", "output", "line", "lines", "key", "keys", "entry", "entries",
    "source", "sources", "evidence", "result", "results", "both", "three",
    "two", "all", "each", "same", "differ", "differs", "match", "matches",
    "shows", "show", "states", "state", "passed", "failed",
    "файл", "файла", "файлы", "файлов", "значение", "значения", "значений",
    "сумма", "сумму", "сумме", "итог", "счёт", "содержит", "содержат",
    "совпадают", "совпадает", "отчёт", "отчёте", "тест", "тесты", "тестов",
    "вывод", "строка", "строки", "строк", "ключ", "ключи", "ключей",
    "запись", "записи", "источник", "источника", "источники", "улика",
    "улики", "результат", "результата", "оба", "обе", "все", "каждый",
    "совпали", "различаются", "показывает", "сказано",
})


#: Префиксы, чья улика — проза: против кода и структурированного вывода сравнение слов
#: ничего не доказывает и перехватывало бы вердикт у более точных гейтов.
_PROSE_PREFIXES: frozenset[str] = frozenset({"web", "search"})


def off_topic_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если разрешённая цитата не про это утверждение (шестой гейт, MIR-060).

    Демоция только при нуле общих содержательных слов: ложный отказ дорог,
    а пересказ почти всегда оставляет хоть одно общее слово.
    """
    if prefix not in _PROSE_PREFIXES:
        return None
    excerpt_raw = ev.excerpt or ""
    # R8: общее слово могло быть в отрезанной части.
    if ("[INTENT-BUDGET:" in excerpt_raw or "[TOTAL-BUDGET:" in excerpt_raw
            or excerpt_raw.rstrip().endswith("...[truncated]")):
        return None
    if not excerpt_raw.strip():
        return None
    # Требование числа снято (проба); разноязычие и мета-утверждения отсекаются ниже.
    claim_tokens = _subject_tokens(_CITATION_TOKEN_RE.sub(" ", chunk_text))
    # Кусок про сами улики: отсутствие общих слов ничего не доказывает.
    if claim_tokens and claim_tokens <= _META_VOCABULARY:
        return None
    # Мета-слова не вычитаются: «запись», «файл» — и обычные существительные.
    if len(claim_tokens) < _OFF_TOPIC_MIN_TOKENS:
        return None
    # Разные алфавиты — не разные предметы: агент читает по-английски, отвечает по-русски.
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    known = _subject_tokens(excerpt_raw) | _subject_tokens(ev.source_id or "")
    # Порог доли невозможен: естественный пересказ делит с уликой так же мало слов,
    # как чужая тема.
    if claim_tokens & known:
        return None
    from .verifier_models import ClaimReason

    return ClaimReason(
        code="cited_source_off_topic",
        expected=", ".join(sorted(known)[:3]) or "(улика без содержательных слов)",
        actual=", ".join(sorted(claim_tokens)[:3]),
        explanation=(
            "цитата разрешилась, но улика не про это утверждение: "
            "ни одного общего содержательного слова"
        ),
        computed_from=ev.source_id or "",
    )


#: Мета-отрицание: обороты, объявляющие неверным всё утверждение, а не его член
#: («не менее», «а не 30» сюда не входят).
_META_DENIAL_RE = re.compile(
    r"(?:\bневерн\w*"
    r"|\bэто\s+не\s+так\b"
    r"|\bна\s+самом\s+деле\s+(?:это\s+)?не\s+так\b"
    r"|\bне\s+соответству\w*\s+действительности"
    r"|\bошибочн\w*"
    r"|\bis\s+not\s+true\b"
    r"|\bis\s+false\b"
    r"|\b(?:which|that)\s+is\s+(?:not\s+true|false|incorrect)\b"
    r"|\bincorrect\b)",
    re.IGNORECASE,
)


def denies_own_evidence_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если утверждение объявляет неверной свою же улику (девятый гейт).

    Если отрицание несёт и сама улика, утверждение его лишь передаёт.
    """
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    excerpt_raw = ev.excerpt or ""
    if not excerpt_raw.strip():
        return None
    # Лексикон отрицания языкозависим: отрицание в улике на другом языке не видно.
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    body = _CITATION_TOKEN_RE.sub(" ", chunk_text)
    if not _META_DENIAL_RE.search(body):
        return None
    if _META_DENIAL_RE.search(excerpt_raw):
        return None  # улика сама отрицает — утверждение её передаёт
    # Без общего предмета это вопрос гейта «не по теме».
    if not (_subject_tokens(body) & _subject_tokens(excerpt_raw)):
        return None
    from .verifier_models import ClaimReason

    return ClaimReason(
        code="claim_denies_its_evidence",
        expected="улика утверждает то, что кусок объявляет неверным",
        actual=_META_DENIAL_RE.search(body).group(0),
        explanation=(
            "утверждение ссылается на улику и одновременно объявляет её "
            "неверной: такая ссылка не может быть его поддержкой"
        ),
        computed_from=ev.source_id or "",
    )


#: Число в тексте; при приближении («около 20») оно не судится.
_BARE_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

#: Точка между цифрами — десятичный разделитель, а не конец предложения.
_SENTENCE_SPLIT = re.compile(r"(?<![0-9])[.!?]+(?![0-9])|" + chr(10) + "+")

_APPROXIMATION_RE = re.compile(
    r"(?:\bоколо\b|\bпримерно\b|\bприблизительно\b|\bпорядка\b|~"
    r"|\bapproximately\b|\babout\b|\broughly\b|\bcirca\b)",
    re.IGNORECASE,
)

#: Доля слов утверждения в предложении улики, при которой кусок — дословный пересказ.
_RESTATEMENT_OVERLAP = 0.8


def restated_number_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если кусок повторяет предложение улики, изменив число (десятый гейт).

    Не правило присутствия: вычисленные числа и границы сравнения законно
    отсутствуют в улике (MIR-143), поэтому судится только почти дословный пересказ.
    """
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    excerpt_raw = ev.excerpt or ""
    if not excerpt_raw.strip():
        return None
    if ("[INTENT-BUDGET:" in excerpt_raw or "[TOTAL-BUDGET:" in excerpt_raw
            or excerpt_raw.rstrip().endswith("...[truncated]")):
        return None
    body = _CITATION_TOKEN_RE.sub(" ", chunk_text)
    if _APPROXIMATION_RE.search(body):
        return None
    if _dominant_script(body) != _dominant_script(excerpt_raw):
        return None
    claim_words = _subject_tokens(body)
    if len(claim_words) < 3:
        return None
    claimed = {m.group(0) for m in _BARE_NUMBER_RE.finditer(body)}
    if not claimed:
        return None

    for sentence in _SENTENCE_SPLIT.split(excerpt_raw):
        if not sentence.strip():
            continue
        words = _subject_tokens(sentence)
        if not words:
            continue
        shared = len(claim_words & words) / len(claim_words)
        if shared < _RESTATEMENT_OVERLAP:
            continue
        # Кусок повторяет это предложение: разница может быть только в числах.
        known = {m.group(0) for m in _BARE_NUMBER_RE.finditer(sentence)}
        missing = {n for n in claimed if not any(n == k or n in k for k in known)}
        if not missing:
            return None
        from .verifier_models import ClaimReason

        return ClaimReason(
            code="restated_number_changed",
            expected=", ".join(sorted(known)[:3]) or "(в предложении нет чисел)",
            actual=", ".join(sorted(missing)[:3]),
            explanation=(
                "кусок повторяет предложение улики и расходится с ним только "
                "числом"
            ),
            computed_from=ev.source_id or "",
        )
    return None


#: Утверждение об отсутствии («нет X», «not implemented»); маркер ищется по всему куску.
_ABSENCE_ASSERTION_RE = re.compile(
    r"(?:\bотсутству\w*|\bне\s+(?:найден\w*|реализован\w*|существу\w*|"
    # Глаголы отсутствия механизма («не обрабатывает», «не предусмотрены»).
    r"содерж\w*|определ\w*|предусмотр\w*|обрабатыва\w*|поддержива\w*|"
    r"учитыва\w*)|\bнет\b|\bни\s+одного\b"
    r"|\bno\s+(?:\S+\s+){1,3}(?:exists?|found|implemented)|\bnot\s+(?:implemented|"
    # Те же глаголы по-английски, и `no handling` без завершающего глагола.
    r"found|present|defined|exist)|\bdoes\s+not\s+(?:exist|contain|define|"
    r"implement|handle|support|check)"
    r"|\bno\s+(?:handling|support|validation|check|mechanism)\b"
    r"|\babsent\b|\bmissing\b)",
    re.IGNORECASE,
)

#: Предмет, названный явно в кавычках или апострофах; прозаический предмет не судится намеренно.
_NAMED_SUBJECT_RE = re.compile(r"[`\"']([A-Za-z_][A-Za-z0-9_.]{2,})[`\"']")


#: Часть предложения, утверждающая наличие своего предмета: он не предмет отсутствия.
_PRESENCE_RE = re.compile(
    r"(?:(?<!не\s)\bнайден\w*|\bпрочита\w*|\bпрочё?л\w*|\bзавершил\w*|\bесть\b"
    r"|(?<!не\s)\bсуществу\w*|(?<!не\s)\bприсутству\w*|\bна\s+месте\b"
    r"|\bзаписан\w*|\bсоздан\w*|\bread\b|\bfound\b|\bexists?\b|\bis\s+present\b"
    r"|\bcompleted\b|\bfinished\b|\bwritten\b|\bcreated\b"
    # Назвать — значит сказать, что есть («клиент называется `LLM`»).
    r"|(?<!не\s)\bназыва\w*|(?<!не\s)\bзов[её]т\w*|\bis\s+called\b|\bis\s+named\b)",
    re.IGNORECASE,
)
#: Границы частей сложного предложения: запятая, точка с запятой, тире.
_CLAUSE_SPLIT_RE = re.compile(r"[,;]|\s[—–]\s")
#: Место поиска («в `stderr` нет ошибок», «по файлу x.txt»): оно существует, отсутствует искомое.
_SEARCH_PLACE_RE = re.compile(
    r"\b(?:в|во|по|in|inside|within|across)\s+"
    r"(?:(?:файле|файлу|файлам|файлах|книге|тексте|логе|журнале|(?:the\s+)?(?:file|book|log|text))\s+)?"
    r"(?:`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'"
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv))",
    re.IGNORECASE,
)


#: Перечень найденного после слова о находке — не предмет отсутствия.
#: Конец — точка с пробелом, чтобы точка внутри `core/loader.py` его не обрывала.
_FOUND_MATERIAL_RE = re.compile(
    r"(?:совпадени\w*|результат\w*|matches|match|hits|найден\w*|found)\b(?:(?!\.\s)[^;\n])*",
    re.IGNORECASE)


#: Показанный материал — скобочный перечень и «ёлочки»; апострофы и прямые
#: кавычки не входят: ими называют сам предмет (`itertools.batched`).
_ENUMERATED_SPAN_RE = re.compile(r"\([^()\n]*\)|«[^»\n]*»")

#: Хвост сообщения исключения до конца предложения — цитата прибора, а не предмет.
_ERROR_TAIL_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b[^.;\n]*")


def absence_subjects(claim: str) -> set[str]:
    """Явно названные предметы утверждения об отсутствии.

    Не предметы: то, что утверждается наличным, места поиска и найденное.
    """
    # Перечисленное вырезается до разбора по частям: запятая в скобках рвёт перечень.
    body = _ENUMERATED_SPAN_RE.sub(" ", _CITATION_TOKEN_RE.sub(" ", claim or ""))
    # Голые литералы берутся только из части с отрицанием; из соседних — лишь
    # названное явно и без хвоста сообщения об ошибке.
    parts = _CLAUSE_SPLIT_RE.split(body)
    denying = [bool(_ABSENCE_ASSERTION_RE.search(_own_voice(p))) for p in parts]
    kept = [
        part if deny else _ERROR_TAIL_RE.sub(" ", part)
        for part, deny in zip(parts, denying, strict=True)
        if deny or not _PRESENCE_RE.search(part)
    ]
    text = _FOUND_MATERIAL_RE.sub(" ", _SEARCH_PLACE_RE.sub(" ", " , ".join(kept)))
    denied = _FOUND_MATERIAL_RE.sub(" ", _SEARCH_PLACE_RE.sub(
        " ", " , ".join(p for p, deny in zip(parts, denying, strict=True) if deny)))
    named = {m.group(1).lower() for m in _NAMED_SUBJECT_RE.finditer(text)}
    return named | salient_literals(denied)


#: Чужой голос внутри утверждения: цитаты в кавычках/апострофах и хвост сообщения исключения.
_QUOTED_SPAN_RE = re.compile(
    r"`[^`\n]*`"
    r"|«[^»\n]*»"
    r'|"[^"\n]*"'
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b[^.;\n]*",
)


def _own_voice(claim: str) -> str:
    """Текст утверждения без цитат и хвостов сообщений об ошибках."""
    return _QUOTED_SPAN_RE.sub(" ", claim or "")


def asserts_absence(claim: str) -> bool:
    """Утверждает ли кусок СВОИМ ГОЛОСОМ, что чего-то нет."""
    return bool(_ABSENCE_ASSERTION_RE.search(_own_voice(claim)))


def absence_refuted_by_excerpt(claim: str, excerpt: str) -> bool:
    """Опровергает ли цитируемая улика утверждение об отсутствии."""
    if not claim or not excerpt or not asserts_absence(claim):
        return False
    body = excerpt.lower()
    return any(subject in body for subject in absence_subjects(claim))


def absence_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если улика содержит то, чего утверждение не нашло."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    from .verifier_utils import truth_excerpt

    outcome = truth_excerpt(ev.excerpt or "")
    if not absence_refuted_by_excerpt(chunk_text, outcome):
        return None
    # Опровергнуть отсутствие может только само названное место, а не другой источник.
    places = _denied_places(chunk_text)
    if places and _cited_path(ev.source_id or "") not in places:
        return None
    present = sorted(
        s for s in absence_subjects(chunk_text) if s in outcome.lower()
    )
    from .verifier_models import ClaimReason
    return ClaimReason(
        code="absence_refuted_by_evidence",
        expected="",
        actual=", ".join(present[:3]),
        explanation="утверждение об отсутствии опровергнуто собственной уликой",
        computed_from=ev.source_id or "",
    )

_PLACE_PATH_RE = re.compile(
    r"[\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv|log)\b", re.IGNORECASE)
#: Место — путь после «в/in» не дальше трёх слов и не после «про/about»
#: (там путь — предмет, а не место).
_PLACE_RE = re.compile(
    r"\b(?:в|во|in|inside|within)\s+"
    r"(?:(?!(?:про|о|об|about|for)\b)[^\s,;:`'\"«]+\s+){0,3}"
    r"[`'\"«]?([\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv|log))\b",
    re.IGNORECASE)


def _denied_places(claim: str) -> set[str]:
    """Файлы, названные МЕСТОМ в той части утверждения, где стоит отрицание."""
    body = _CITATION_TOKEN_RE.sub(" ", claim or "")
    return {
        m.group(1).replace("\\", "/").lower()
        for part in _CLAUSE_SPLIT_RE.split(body)
        if _ABSENCE_ASSERTION_RE.search(_own_voice(part))
        for m in _PLACE_RE.finditer(part)
    }


def _cited_path(source_id: str) -> str:
    """Путь цитируемого источника без префикса вида и без диапазона строк."""
    rest = source_id.split(":", 1)[-1] if ":" in source_id else source_id
    found = _PLACE_PATH_RE.search(rest)
    return found.group(0).replace("\\", "/").lower() if found else ""


def absence_certifiable(claim: str, excerpt: str) -> bool:
    """Можно ли вообще СЕРТИФИЦИРОВАТЬ утверждение об отсутствии выдержкой."""
    return not asserts_absence(claim)


#: Отчёт поиска о ненайденном (tools/find_in_files.py): искомое и область.
_SEARCH_NONE_RE = re.compile(
    r"no matches for (['\"])(?P<query>.+?)\1 in \d+ text files under (?P<scope>\S+)")


#: Ответ «ресурса нет» (tools/web_fetch.py, 404/410): код и адрес.
_NOT_FOUND_RE = re.compile(r"^HTTP (?:404|410)\b[^:]*: (?P<url>\S+)")
#: Части адреса, которые называют не предмет, а устройство сайта.
_GENERIC_SEGMENTS = frozenset({"json", "simple", "project", "projects", "pypi", "api", "v1", "v2",
                               "www", "http", "https", "index", "search", "package", "packages"})


def absence_certified_by_not_found(claim: str, evidences: list[Any]) -> bool:
    """Отсутствие доказано ответом 404/410 от web_fetch на адрес, где назван предмет.

    Узко: только если часть пути, называющая предмет, стоит в утверждении.
    Страница-ошибка — источник только для «по этому адресу ничего нет» (обход H-01).
    """
    if not asserts_absence(claim):
        return False
    low = (claim or "").lower()
    for ev in evidences or []:
        if getattr(ev, "obtained_via", "") != "web_fetch":
            continue
        m = _NOT_FOUND_RE.search(getattr(ev, "excerpt", "") or "")
        if not m:
            continue
        # Имя сайта предмет не называет — смотрится только путь.
        path = m.group("url").split("://", 1)[-1].split("?", 1)[0].lower().partition("/")[2]
        segments = [s for s in re.split(r"[/.]", path) if len(s) >= 4 and s not in _GENERIC_SEGMENTS]
        if any(s in low for s in segments):
            return True
    return False


def absence_certified_by_search(claim: str, evidences: list[Any]) -> bool:
    """Отсутствие доказано полным поиском: искомое и область названы в утверждении.

    Отчёт find_in_files — не усечённая выдержка: инструмент прошёл всю область (MIR-060 (e)).
    """
    if not asserts_absence(claim):
        return False
    low = (claim or "").lower().replace("\\", "/")
    for ev in evidences or []:
        if getattr(ev, "obtained_via", "") != "find_in_files":
            continue
        m = _SEARCH_NONE_RE.search(getattr(ev, "excerpt", "") or "")
        if not m or m.group("query").lower() not in low:
            continue
        scope = m.group("scope").replace("\\", "/").rstrip("/").lower()
        if scope in (".", "") or scope in low:
            return True
    return False
