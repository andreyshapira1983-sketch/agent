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
_SALIENT_LITERAL_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
    r"|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]*\d[A-Za-z0-9]*)+(?:-[A-Za-z0-9]+)*"
    r"|[0-9a-f]{7,40}"
    r"|\d+(?:\.\d+){2,}"
    r"|[\w./\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml))\b"
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


def _subject_tokens(text: str) -> set[str]:
    """Содержательные слова БЕЗ голых чисел.

    Число общим предметом не делает: «Курс акций вырос на 20 процентов» и
    «Задержка составила 20 миллисекунд» делят токен `20` и не делят ничего
    больше. Это тот же урок, что и в MIR-141 — цифра не есть тема.
    """
    from .topic_tokens import topic_tokens

    return {t for t in topic_tokens(text) if not t.isdigit()}


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
    if prefix in {"user", "memory", "general-knowledge"}:
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
    from .verifier_utils import extract_statistical_figures

    if not extract_statistical_figures(chunk_text):
        return None
    claim_tokens = _subject_tokens(_CITATION_TOKEN_RE.sub(" ", chunk_text))
    if len(claim_tokens) < _OFF_TOPIC_MIN_TOKENS:
        return None
    known = _subject_tokens(excerpt_raw) | _subject_tokens(ev.source_id or "")
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


def absence_subjects(claim: str) -> set[str]:
    """Явно названные предметы утверждения об отсутствии."""
    named = {m.group(1).lower() for m in _NAMED_SUBJECT_RE.finditer(claim or "")}
    return named | salient_literals(_CITATION_TOKEN_RE.sub(" ", claim or ""))


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
