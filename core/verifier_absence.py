"""Certifying a claim of ABSENCE — the half of verification a citation cannot
do.
"""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence

#: Отличительные литералы: идентификаторы моделей и прогонов, SHA, пути к файлам,
#: версионные триплеты (`3.11.9` — две точки и больше).
#: Голые числа и десятичные (`3.12`, `0.795`) СОЗНАТЕЛЬНО не входят — счёт,
#: сумма и сравнение принадлежат `evaluate_claim_arithmetic`, и второй судья
#: над той же областью спорил бы с первым. Триплет — не число, а имя: живая
#: проба 2026-08-16 (trace_3bb22486) — «у меня Python 3.11.9» проехало
#: verified зайцем на цитате, знавшей только про 3.12; см. CODE_NOTES,
#: «The stowaway claim».
#: Форма собрана по предметам, наблюдавшимся живьём: `claude-…`,
#: `run_…`, `trace_…`, SHA коммита, `core/…​.py`.
#: Через дефис — ТОЛЬКО с цифрой внутри. Измерено: без этого условия обычная
#: проза («goal-directed», «read-only», «fail-before») читается как
#: идентификатор, и гейт демотирует верные утверждения. Через подчёркивание
#: цифра не нужна: `AWS_SECRET_KEY` словом не бывает.
#: CodeQL #22 (2026-08-28): прежняя форма давала ×16 на вход ×4 по трём
#: стенам — двусмысленные разрезы дефисной ветки и безразмерный прогон
#: файловой. Лечение: посессивные кванторы (разрезы умирают) и потолки длины
#: {,160} — реальный литерал короче, а ограниченный повтор превращает квадрат
#: в линию с константой. Семантика приколочена соседним тестом с обеих сторон.
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
    # Маркеры ссылок вырезаются ПЕРВЫМ делом. Внутри `[file:core/loop.py]`
    # лежит адрес источника, а не утверждение о мире; оставь его — и всякая
    # цитата, чей source_id не встречается в собственной выдержке, оказалась
    # бы «литералом, которого нет в улике». Измерено: без этой строки гейт
    # демотировал истинный контроль.
    body = _CITATION_TOKEN_RE.sub(" ", claim)
    # Имя СОБСТВЕННОГО источника чужим литералом не считается: «набор прошёл в
    # tests/bug_lab [test:run_tests:bug_lab]» называет адрес, который цитата и
    # устанавливает. Измерено: без этого гейт демотировал три существующие
    # фикстуры, где проза повторяла имя цитируемого источника.
    known = salient_literals(excerpt) | salient_literals(source_id or "")
    return {
        lit for lit in salient_literals(body)
        if lit not in known and not any(lit in k or k in lit for k in known)
    }


def absent_literal_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если утверждение называет то, чего нет в улике."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    # Утверждение об отсутствии судит ПЯТЫЙ гейт, и по обратному правилу:
    # там литерал в улике отсутствует именно потому, что его нет. Проверять
    # его присутствием значило бы демотировать верное утверждение — ложное
    # срабатывание, внесённое вместе с этим гейтом 2026-08-10.
    if asserts_absence(chunk_text):
        return None
    # R8 (2026-08-13, живой bd02fff1): усечённая бюджетом улика не доказывает
    # отсутствия — литерал мог жить в отрезанной части (семь ложных REFUTED за
    # один ход). Присутствие в вырезке она доказывает по-прежнему.
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
    return ClaimReason(
        code="cited_literal_absent",
        expected=", ".join(sorted(absent)[:3]),
        actual="",
        explanation="утверждение называет то, чего нет в цитируемой улике",
        computed_from=ev.source_id or "",
    )


#: Сколько содержательных слов должно быть в куске, чтобы расхождение с уликой
#: что-то значило. У «Да.» или «Готово.» общих слов нет ни с чем, и молчание
#: здесь — не снисходительность, а отсутствие предмета для суждения.
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
    """Содержательные слова БЕЗ голых чисел.

    Число общим предметом не делает: «Курс акций вырос на 20 процентов» и
    «Задержка составила 20 миллисекунд» делят токен `20` и не делят ничего
    больше. Это тот же урок, что и в MIR-141 — цифра не есть тема.
    """
    from .topic_tokens import discriminating_tokens

    # `discriminating_tokens`, а не `topic_tokens`: стоп-лист применяет только
    # первая. Замерено — со второй английские артикли и связки («the», «was»)
    # считались общим предметом, и гейт молчал на любом англоязычном
    # расхождении: «The share price rose by 20 percent» делило с уликой про
    # задержку ровно «the» и проезжало как подтверждённое.
    return {t for t in discriminating_tokens(text) if not t.isdigit()}


#: Словарь МЕТА-утверждений: слова не про мир, а про сами улики и действия над
#: ними — «both files agree», «the three values sum to 6», «в отчёте сказано».
#: У такого куска содержательных слов о предмете нет вовсе, и расхождение с
#: выдержкой ничего не значит: судить его — работа вычисляющего гейта или
#: человека, но не сравнения слов.
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


#: Префиксы, чья улика — ПРОЗА. Сравнение слов имеет смысл только против
#: прозы: у файла, прогона тестов, лога, диффа и вывода оболочки выдержка
#: состоит из кода и структурированных строк, и отсутствие общих слов с
#: предложением ничего не доказывает. Замерено: без этого сужения гейт
#: демотировал три существующие фикстуры и, хуже того, ПЕРЕХВАТЫВАЛ вердикт у
#: более точного гейта (кусок, у которого нет квитанции, получал topic-only
#: вместо receipt_missing).
_PROSE_PREFIXES: frozenset[str] = frozenset({"web", "search"})


def off_topic_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если разрешённая цитата НЕ ПРО это утверждение.

    Шестой гейт лестницы MIR-060. Пятеро предыдущих судят утверждения о
    файлах, идентификаторах, числах и отсутствии; на прозе они структурно
    немы, потому что `_SALIENT_LITERAL_RE` признаёт литералами только
    код-образное. Замер дискриминации 2026-08-23 дал по оси «утверждение по
    теме цепочки» J = 0.00: утверждение о курсе акций, процитированное на
    источник о задержке, принималось как подтверждённое в 100 % случаев.

    Правило намеренно грубое: демоция только при ПОЛНОМ отсутствии общих
    содержательных слов. Не «мало», не «ниже порога» — ноль. Ложный отказ
    дорог (в тот же день он стоил нам канала с цифрой в адресе цитаты), а
    пересказ, синоним или другая падежная форма почти всегда оставляют хоть
    одно общее слово. Гейт может только снять ложное `verified`, никогда не
    создать его.
    """
    if prefix not in _PROSE_PREFIXES:
        return None
    excerpt_raw = ev.excerpt or ""
    # Та же оговорка, что у четвёртого гейта (R8): усечённая бюджетом улика не
    # доказывает расхождения — общее слово могло жить в отрезанной части.
    if ("[INTENT-BUDGET:" in excerpt_raw or "[TOTAL-BUDGET:" in excerpt_raw
            or excerpt_raw.rstrip().endswith("...[truncated]")):
        return None
    if not excerpt_raw.strip():
        return None
    # Гейт судит только КОНКРЕТНОЕ количественное утверждение. Замерено на
    # существующем корпусе: без этого условия он демотировал два законных
    # класса — (1) русское утверждение по англоязычной улике, где общих слов
    # нет по причине языка, а не предмета (наш агент так и работает: читает
    # по-английски, отвечает по-русски); (2) мета-утверждение об уликах
    # («Both files agree»), где слова про отношение, а выдержка — содержимое.
    # Число делает утверждение проверяемым и обязывает его быть про свой
    # источник; без числа судить не о чем, и молчание честнее.
    # ПРОБА: требование числа снято
    claim_tokens = _subject_tokens(_CITATION_TOKEN_RE.sub(" ", chunk_text))
    # Кусок ПРО САМИ УЛИКИ, а не про мир: «both files agree», «the three values
    # sum to 6». Слова такого куска описывают действие над выдержкой, а не её
    # предмет, поэтому отсутствие общих слов ничего не доказывает. Замерено:
    # без этой оговорки гейт демотировал шесть существующих фикстур, среди них
    # верное производное утверждение, у которого есть СВОЙ судья.
    if claim_tokens and claim_tokens <= _META_VOCABULARY:
        return None
    # ВЫЧИТАТЬ мета-слова из утверждения нельзя, и это стоило отдельного
    # замера. «Запись», «источник», «результат», «строка», «файл» — обычные
    # русские существительные, и вычитание их из куска искусственно обнуляло
    # пересечение: проба класса H поймала «Запись концерта выложили» по этой
    # причине, то есть по неправильной. Мета-словарь отвечает на вопрос «весь
    # ли кусок про улики», и только на него.
    if len(claim_tokens) < _OFF_TOPIC_MIN_TOKENS:
        return None
    # РАЗНЫЕ АЛФАВИТЫ — не разные предметы. Замерено 2026-08-23, через час
    # после того, как этот гейт был поставлен: два из трёх ВЕРНЫХ русских
    # утверждений по английской улике оказались демотированы, а это ежедневная
    # форма работы агента — он читает по-английски и отвечает по-русски.
    # Требование числа, на которое я тогда положился, от этого не спасает:
    # число как раз и включает гейт. Совпадения слов между языками не бывает,
    # поэтому судить здесь нельзя вовсе.
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    known = _subject_tokens(excerpt_raw) | _subject_tokens(ev.source_id or "")
    # ПОРОГ ДОЛИ ЗДЕСЬ НЕВОЗМОЖЕН, и это замерено, а не выведено. Развёртка
    # 0.20 / 0.34 / 0.50 против класса H (утверждение не по теме, делящее одно
    # случайное слово): 0.34 закрывает ось и отвергает верный пересказ
    # «Квартальный доход достиг 20 миллионов»; 0.20 оставляет ось наполовину
    # открытой и всё равно отвергает два более длинных верных пересказа с
    # долей 0.12 и 0.14. Чем естественнее пересказ, тем ниже его доля — тот же
    # профиль, что у чужой темы. Различие семантическое, лексикой не берётся.
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


#: МЕТА-ОТРИЦАНИЕ: отрицание пропозиции ЦЕЛИКОМ, а не члена предложения.
#: «не превысила», «не менее», «а не 30» — обычные отрицания, они спорят с
#: числом или с альтернативой, но не с источником. Здесь перечислены только
#: обороты, которыми утверждение объявляет НЕВЕРНЫМ то, на что ссылается.
#: Это лексикон, а не вывод — тот же приём, каким `claim_arithmetic` узнаёт
#: свои формы, и по той же причине: судить по сходству нельзя.
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
    """Причина демоции, если утверждение объявляет неверной СВОЮ ЖЕ улику.

    Девятый гейт. Замер 2026-08-23 дал по оси полярности J = 0.00:
    предложение, повторяющее источник и добавляющее «— неверно, это не так»,
    принималось как подтверждённое в 100 % случаев.

    Полярность считается У ОБОИХ. Если улика сама несёт отрицание, утверждение
    его лишь передаёт, и спора нет — поэтому маркер ищется не в одном тексте,
    а сравнивается между текстами. Без этого правило демотировало бы честный
    пересказ отрицательного источника.
    """
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    excerpt_raw = ev.excerpt or ""
    if not excerpt_raw.strip():
        return None
    # Разные алфавиты: лексикон отрицания языкозависим, и отрицание в улике на
    # другом языке этот гейт просто не увидит — молчание честнее догадки.
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    body = _CITATION_TOKEN_RE.sub(" ", chunk_text)
    if not _META_DENIAL_RE.search(body):
        return None
    if _META_DENIAL_RE.search(excerpt_raw):
        return None  # улика сама отрицает — утверждение её передаёт
    # Спор бывает только о том, что улика утверждает: без общего предмета это
    # вопрос седьмого гейта, а не этого.
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


#: Приближение — не подмена: «около 20» при 19.8 остаётся верным, и число в
#: улике искать бессмысленно.
_BARE_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

#: Точка между цифрами — десятичный разделитель, а не конец предложения.
#: Замерено: без этого «19.8» распадалось на «19» и «8», предложение улики
#: не находилось, и гейт молчал на любой улике с десятичным числом.
_SENTENCE_SPLIT = re.compile(r"(?<![0-9])[.!?]+(?![0-9])|" + chr(10) + "+")

_APPROXIMATION_RE = re.compile(
    r"(?:\bоколо\b|\bпримерно\b|\bприблизительно\b|\bпорядка\b|~"
    r"|\bapproximately\b|\babout\b|\broughly\b|\bcirca\b)",
    re.IGNORECASE,
)

#: Какая доля содержательных слов утверждения должна найтись в предложении
#: улики, чтобы считать кусок ДОСЛОВНЫМ пересказом именно его. Не половина и
#: не «похоже»: форма судит только там, где расхождение сведено к числу.
_RESTATEMENT_OVERLAP = 0.8


def restated_number_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Причина демоции, если кусок повторяет предложение улики, изменив число.

    Десятый гейт. Замер 2026-08-23 дал по оси «число соответствует источнику»
    J = +0.25: «Выручка за квартал составила 999 миллионов рублей» при улике с
    20 принималось как подтверждённое.

    ЭТО НЕ ПРАВИЛО ПРИСУТСТВИЯ, и различие проверяется границами. Правило
    «всякое число обязано найтись в улике» было построено, дало по этой оси
    +1.00 и уронило стенд способностей с 29 до 27: 117 в «117 tests passed»
    ВЫЧИСЛЕНО из 120−3, а 3.0 в «версия ниже 3.0» есть ГРАНИЦА сравнения, и оба
    законно отсутствуют (анти-требование в MIR-143). Здесь форма именованная:
    утверждение почти дословно повторяет предложение улики, и тогда число —
    единственное, что оно добавляет от себя. Производное, сравнительное и любой
    пересказ другими словами под форму не подходят и не судятся.
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
        # Это предложение кусок и повторяет. Его числа — единственная
        # оставшаяся разница.
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


#: Утверждение ОБ ОТСУТСТВИИ: «нет X», «X отсутствует», «не найдено X»,
#: «not implemented», «no X exists». Маркер ищется по всему куску, а не в
#: начале: отрицание в русском и английском стоит где угодно.
_ABSENCE_ASSERTION_RE = re.compile(
    r"(?:\bотсутству\w*|\bне\s+(?:найден\w*|реализован\w*|существу\w*|"
    # +2026-08-15: глаголы отсутствия МЕХАНИЗМА. Живой прогон доложил «код не
    # обрабатывает случаи низкой уверенности» и «не предусмотрены меры» — та же
    # форма, что «не реализовано», и шаблон её не знал.
    r"содерж\w*|определ\w*|предусмотр\w*|обрабатыва\w*|поддержива\w*|"
    r"учитыва\w*)|\bнет\b|\bни\s+одного\b"
    r"|\bno\s+(?:\S+\s+){1,3}(?:exists?|found|implemented)|\bnot\s+(?:implemented|"
    # +2026-08-15: английская сторона догоняет русскую — те же глаголы
    # отсутствия механизма, и `no handling` без завершающего глагола.
    r"found|present|defined|exist)|\bdoes\s+not\s+(?:exist|contain|define|"
    r"implement|handle|support|check)"
    r"|\bno\s+(?:handling|support|validation|check|mechanism)\b"
    r"|\babsent\b|\bmissing\b)",
    re.IGNORECASE,
)

#: Предмет утверждения об отсутствии: то, что названо ЯВНО — в кавычках,
#: обратных апострофах, либо отличительным литералом. Прозаический предмет
#: («нет обработки ошибок») сюда не попадает намеренно: это суждение о смысле,
#: и ловить его сверкой подстрок значило бы выдумать судью.
_NAMED_SUBJECT_RE = re.compile(r"[`\"']([A-Za-z_][A-Za-z0-9_.]{2,})[`\"']")


#: Часть предложения, которая сама утверждает НАЛИЧИЕ или удачное действие над
#: своим предметом: «`data.csv` найден», «прочитал `data.csv`», «завершился с
#: `exit_code: 0`». Её предмет не может быть предметом отсутствия.
_PRESENCE_RE = re.compile(
    r"(?:(?<!не\s)\bнайден\w*|\bпрочита\w*|\bпрочё?л\w*|\bзавершил\w*|\bесть\b"
    r"|(?<!не\s)\bсуществу\w*|(?<!не\s)\bприсутству\w*|\bна\s+месте\b"
    r"|\bзаписан\w*|\bсоздан\w*|\bread\b|\bfound\b|\bexists?\b|\bis\s+present\b"
    r"|\bcompleted\b|\bfinished\b|\bwritten\b|\bcreated\b)",
    re.IGNORECASE,
)
#: Границы частей сложного предложения: запятая, точка с запятой, тире.
_CLAUSE_SPLIT_RE = re.compile(r"[,;]|\s[—–]\s")
#: МЕСТО поиска: «в `stderr` нет ошибок», «в `x.py` нет функции foo». Место
#: предполагается существующим — искать можно только в том, что есть; отсутствует
#: то, что искали.
_SEARCH_PLACE_RE = re.compile(
    r"\b(?:в|во|in|inside)\s+(?:`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'"
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv))",
    re.IGNORECASE,
)


def absence_subjects(claim: str) -> set[str]:
    """Явно названные предметы утверждения об отсутствии.

    Замер 2026-09-19 (одна задача пять раз подряд): «Скрипт завершился с кодом
    0, ошибок нет, входной файл `data.csv` найден» — «нет» относится к ошибкам,
    а предметом отсутствия считался `data.csv` из соседней части; улика его
    содержала, и верное утверждение получало absence_refuted_by_evidence.
    Предметы частей, которые сами утверждают наличие, не судятся. Следующий
    прогон: «ошибок в `stderr` нет» — предметом стал `stderr`, место поиска.
    """
    body = _CITATION_TOKEN_RE.sub(" ", claim or "")
    kept = [
        part for part in _CLAUSE_SPLIT_RE.split(body)
        if _ABSENCE_ASSERTION_RE.search(_own_voice(part)) or not _PRESENCE_RE.search(part)
    ]
    text = _SEARCH_PLACE_RE.sub(" ", " , ".join(kept))
    named ={m.group(1).lower() for m in _NAMED_SUBJECT_RE.finditer(text)}
    return named | salient_literals(text)


#: Материал, произнесённый ЧУЖИМ голосом внутри утверждения: спаны в обратных
#: апострофах, «ёлочках» и прямых кавычках, и хвост сообщения исключения — от
#: имени класса (`FileNotFoundError`, `ModuleNotFoundError`…) до конца
#: предложения. Это цитаты, а не суждения ответа.
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
    if not absence_refuted_by_excerpt(chunk_text, ev.excerpt or ""):
        return None
    present = sorted(
        s for s in absence_subjects(chunk_text) if s in (ev.excerpt or "").lower()
    )
    from .verifier_models import ClaimReason
    return ClaimReason(
        code="absence_refuted_by_evidence",
        expected="",
        actual=", ".join(present[:3]),
        explanation="утверждение об отсутствии опровергнуто собственной уликой",
        computed_from=ev.source_id or "",
    )

def absence_certifiable(claim: str, excerpt: str) -> bool:
    """Можно ли вообще СЕРТИФИЦИРОВАТЬ утверждение об отсутствии выдержкой."""
    return not asserts_absence(claim)
