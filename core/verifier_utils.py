"""Verifier text utilities: chunk splitting, citation parsing and matching,
and statistical-claim detection.
"""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence, ProvenanceChain

from .verifier_models import Citation, VerificationReport
from .verifier_patterns import (
    _BARE_LIST_MARKER_RE,
    _CITATION_BODY_TOKEN_RE,
    _CITATION_RE,
    _MAX_EXCERPT_FOR_NLI,
    _MD_HEADING_RE,
    _MIN_TOKEN_LEN,
    _NLI_SYSTEM,
    _NO_TOKEN_FALLBACK_PREFIXES,
    _OUTPUT_CONTRACT_HEADER_RE,
    _SENTENCE_SPLIT_RE,
    _STAT_FIGURE_MIN_LEN,
    _STAT_FIGURE_RE,
    _STAT_TRIGGER_RE,
    _SUBAGENT_META_RE,
    _TOKEN_STOPWORDS,
    CITATION_PREFIXES,
)


def _normalise_figure(fig: str) -> str:
    s = fig.lower()
    s = re.sub(r"\s+", "", s)
    return s.replace("\u2013", "-").replace("\u2014", "-")


#: Знаки валюты, приклеенные к числу. Замер 2026-09-23: число утверждения
#: извлекается вместе с ними («$0,171»), а инструменты печатают сумму словом
#: («usd 0.171»), поэтому дословного совпадения не бывает НИКОГДА — и каждое
#: денежное утверждение получало `[claim-figure-unverified]`.
_CURRENCY_GLUE = "$€£₽¥"


def _figure_variants(fig: str) -> set[str]:
    """Написания ОДНОГО И ТОГО ЖЕ числа, которые сверка обязана принять.

    Разбирается ровно две беды, обе замеренные 2026-09-23 на шести стёртых
    ответах (пять из шести — экономическая работа):

    1. **Знак валюты приклеен.** «$0,171» против «usd 0.171» в выводе опыта.
    2. **Десятичная запятая.** Агент отвечает по-русски и пишет «0,171»,
       а инструменты печатают в C-локали «0.171».

    Запятая может быть и разделителем тысяч («1,500» по-английски — это 1500),
    и решать, что именно она значит, здесь НЕ нужно: принимаются оба чтения,
    и с точкой и без разделителя. Сверка от этого не слабеет — она принимает
    больше НАПИСАНИЙ того же числа, но ни одного ДРУГОГО числа.
    """
    base = _normalise_figure(fig)
    if not base:
        return set()
    out = {base, base.lstrip(_CURRENCY_GLUE)}
    for value in list(out):
        if "," in value:
            out.add(value.replace(",", "."))
            out.add(value.replace(",", ""))
        elif "." in value:
            out.add(value.replace(".", ","))
    return {value for value in out if value}


def extract_statistical_figures(text: str) -> list[str]:
    """Числа, которые УТВЕРЖДАЕТ кусок. Адрес цитаты сюда не входит.

    Маркер цитаты снимается перед разбором. До 2026-08-23 он оставался, и
    цифры из пути адреса становились числами утверждения: для
    «…20 миллисекунд. [web:…/p/10]» извлекалось ['20', '10'], лишнего числа в
    источнике не было, и верно процитированный кусок понижался до
    `topic_supported_but_claim_unverified`. Односимвольные хвосты проходили
    лишь потому, что не дотягивали до `_STAT_FIGURE_MIN_LEN` — то есть беда
    росла с длиной идентификатора, а настоящие адреса полны цифр: даты
    `/2026/08/23/`, номера статей, страницы.

    Найдено при замере дискриминации верификатора парами (MIR-141): доля
    принятия ВАЛИДНЫХ ответов держалась ровно на 80 %, и эта ровность выдала
    систематическую причину.
    """
    if not text:
        return []
    text = _CITATION_RE.sub(" ", text)
    if not _STAT_TRIGGER_RE.search(text):
        return []
    figures: list[str] = []
    seen: set[str] = set()
    for m in _STAT_FIGURE_RE.finditer(text):
        raw = m.group(0).strip()
        bare_num = re.fullmatch(r"\d+", raw)
        if bare_num and len(raw) < _STAT_FIGURE_MIN_LEN:
            continue
        norm = _normalise_figure(raw)
        if norm in seen:
            continue
        seen.add(norm)
        figures.append(raw)
    return figures


def is_statistical_claim(text: str) -> bool:
    return bool(text and _STAT_TRIGGER_RE.search(text))


#: Допуск для ПРИБЛИЖЁННОГО числа: «около 20» при 19.8 в улике — верно, а
#: буквального «20» там нет. Освобождать приближения целиком было бы пропуском
#: («около 20» при 500 обязано ловиться), поэтому вместо освобождения —
#: считаемый допуск.
_APPROXIMATION_TOLERANCE = 0.1


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for match in re.finditer(r"\d+(?:[.,]\d+)?", text or ""):
        try:
            out.append(float(match.group(0).replace(",", ".")))
        except ValueError:
            continue
    return out


def _approximately_supported(excerpt: str, figure: str) -> bool:
    """Есть ли в улике число в пределах допуска от заявленного."""
    claimed = _numbers_in(figure)
    if not claimed:
        return False
    target = claimed[0]
    if target == 0:
        return any(abs(value) <= _APPROXIMATION_TOLERANCE for value in _numbers_in(excerpt))
    return any(
        abs(value - target) / abs(target) <= _APPROXIMATION_TOLERANCE
        for value in _numbers_in(excerpt)
    )


def _excerpt_supports_figures(
    excerpt: str, figures: list[str], *, approximate: bool = False
) -> bool:
    if not figures:
        return True
    if not excerpt:
        return False
    excerpt_norm = _normalise_figure(excerpt)
    for figure in figures:
        if any(v in excerpt_norm for v in _figure_variants(figure)):
            continue
        if approximate and _approximately_supported(excerpt, figure):
            continue
        return False
    return True


def _output_contract_header_name(text: str) -> str | None:
    stripped = (text or "").strip()
    match = _OUTPUT_CONTRACT_HEADER_RE.match(stripped)
    if not match:
        return None
    return match.group(1).casefold()


def is_structural_chunk(text: str) -> bool:
    if not text or not text.strip():
        return True
    stripped = text.strip()
    # Кодовый забор — предложение, не утверждение: вне арифметики улик
    # (2026-08-29, серия «учимся программировать себя»). Проза о коде
    # остаётся заявлением и проверяется как раньше.
    if stripped.startswith("```"):
        return True
    if _output_contract_header_name(stripped) is not None:
        return True
    if _MD_HEADING_RE.match(stripped) and "[" not in stripped:
        return True
    return bool(_BARE_LIST_MARKER_RE.match(stripped))


def parse_citations(text: str) -> list[Citation]:
    cits: list[Citation] = []
    for m in _CITATION_RE.finditer(text):
        prefix = m.group(1)
        body = (m.group(2) or "").strip()
        cits.append(Citation(prefix=prefix, body=body, raw=m.group(0), expected_kind=CITATION_PREFIXES[prefix]))
    return cits


def _is_citation_only_chunk(text: str) -> bool:
    cits = parse_citations(text)
    if not cits:
        return False
    remainder = text
    for cit in cits:
        remainder = remainder.replace(cit.raw, "")
    return not remainder.strip(" \t\r\n.,;:-")


def _merge_citation_only_chunks(chunks: list[str]) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        if _is_citation_only_chunk(chunk) and merged and not is_structural_chunk(merged[-1]):
            merged[-1] = f"{merged[-1].rstrip()} {chunk.strip()}"
            continue
        merged.append(chunk)
    return merged


#: Кодовый забор целиком, лениво и без катастрофы: ``` … ``` через (?s).
_FENCE_BLOCK_RE = re.compile(r"(?s)(```.*?```)")


def split_into_chunks(answer: str) -> list[str]:
    """Проза режется на предложения; кодовый забор — атомарный кусок.

    До 2026-08-29 `\\n+` в резаке шинковал рождённый код на псевдо-заявления,
    и стена улик душила творчество (живое удушение run_e4d9a8e8: агент
    спроектировал свой первый тест — страж спрятал 27 «утверждений»). Код —
    предложение, не заявление о мире; его судья — тесты.
    """
    if not answer or not answer.strip():
        return []
    chunks: list[str] = []
    for part in _FENCE_BLOCK_RE.split(answer):
        if not part.strip():
            continue
        if part.lstrip().startswith("```"):
            chunks.append(part.strip())
        else:
            chunks.extend(
                p.strip() for p in _SENTENCE_SPLIT_RE.split(part) if p.strip()
            )
    return chunks


def extract_unresolved_web_urls(report: VerificationReport) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in report.chunks:
        if chunk.verdict != "cited_but_unmatched":
            continue
        for cit in chunk.citations:
            if cit.prefix != "web":
                continue
            url = cit.body.strip()
            if not url:
                continue
            lowered = url.lower()
            if not (lowered.startswith(("http://", "https://"))):
                continue
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
    return ordered


def _tokenise_citation_body(body: str) -> list[str]:
    if not body:
        return []
    raw = [t for t in _CITATION_BODY_TOKEN_RE.split(body.lower()) if t]
    return [t for t in raw if len(t) >= _MIN_TOKEN_LEN and t not in _TOKEN_STOPWORDS]


def _normalise_path(text: str) -> str:
    """One spelling for one file: POSIX separators, no `./`, no trailing slash."""
    out = text.replace("\\", "/").strip().casefold()
    while out.startswith("./"):
        out = out[2:]
    while "//" in out:
        out = out.replace("//", "/")
    return out.rstrip("/")


#: `path:208` or `path:200-215` — the line window a `file_read` label carries.
_LINE_WINDOW_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def _split_window(text: str) -> tuple[str, str]:
    """`core/x.py:200-215` -> (`core/x.py`, `:200-215`); no window -> (`path`, ``)."""
    m = _LINE_WINDOW_SUFFIX_RE.search(text)
    if m is None:
        return text, ""
    return text[: m.start()], m.group(0)


def same_file(cited: str, source_id: str, *, same_window: bool = False) -> bool:
    """Do a citation body and an evidence label name the SAME file?

    Not resolved against the workspace root on purpose: the root is not
    known here, and it is not needed. The sanitiser drops absolute paths from
    tool arguments (`core/step_sanitizer.py`), so a label is always
    relative; only the CITATION varies, and a suffix test settles that
    without new plumbing.

    A line window (`:200-215`) names a part of the file, not another file:
    `[file:core/x.py]` matches evidence `file:core/x.py:200-215` and the
    other way round. With `same_window=True` both windows must also agree —
    the caller's first pass, so a citation of lines 200-215 is matched to
    that window before it falls back to any other read of the same file.
    """
    a, a_window = _split_window(_normalise_path(cited))
    b, b_window = _split_window(
        _normalise_path(source_id.split(":", 1)[-1] if ":" in source_id else source_id)
    )
    if not a or not b:
        return False
    if same_window and a_window != b_window:
        return False
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def runtime_evidence_pool() -> list[Evidence]:
    """What this process measured about itself, as citable Evidence.

    Built on demand and NOT folded into the provenance chain. The first
    attempt did fold it, and 21 tests said why that is wrong: the chain is
    counted and ordered, and its contracts are real — a failed step yields
    no evidence, a `file_write` yields none, a zero-step plan yields an
    EMPTY chain. Five always-present entries break every one of those, and
    they also destroy `chain_was_empty`, which separates "the agent looked
    and found nothing" from "the agent did not look".
    """
    from core.evidence import make_evidence
    from core.run_context import current_run
    from core.runtime_self import process_facts, shown_facts

    # Плюс всё, что блок `<runtime_self>` показал модели В ЭТОМ прогоне:
    # подсказка разрешает цитировать каждое его поле (`core/runtime_self.py`).
    run_id = str(getattr(current_run(), "run_id", "") or "")
    facts = {**shown_facts(run_id), **process_facts()}
    return [
        make_evidence(
            kind="runtime",
            source_id=f"runtime:{key}",
            obtained_via="process_self_measurement",
            claim=f"This run's {key}",
            excerpt=str(value),
        )
        for key, value in facts.items()
    ]


def truth_excerpt(text: str) -> str:
    """Выдержка БЕЗ вопроса опыта: всё после маркера — код, который ход написал сам.

    Разделение внесено 2026-09-20: без кода гейт литералов считал имена
    собственных переменных выдуманными, а с кодом внутри общей выдержки
    вопрос опыта снова подтверждал бы его ответ (docs/CODE_NOTES.md, «The
    experiment's question refuted its answer»).
    """
    from core.evidence import QUESTION_CODE_MARKER

    return str(text or "").split(QUESTION_CODE_MARKER, 1)[0]


def _search_output_evidence(chain: ProvenanceChain) -> list:
    """Выдача `find_in_files` — тоже улика о названных в ней файлах.

    Образец взят у соседа: в `match_citation` для ссылки `web` к кандидатам
    так же добавляются попадания `web_search_hit`.

    Живой случай 2026-09-21: агента попросили найти свои поломки. Он нашёл,
    сослался как положено — `[file:core/x.py:871]`, — и ОДИННАДЦАТЬ из
    двадцати двух утверждений вырезали как выдуманные цитаты. Причина: он
    ИСКАЛ по файлам, а не открывал их, а поиск кладёт всю выдачу в одну
    улику `tool_output:find_in_files`, которая для ссылки вида `file:` даже
    не попадала в кандидаты. Мы требуем ссылок с файлом и строкой — и
    наказывали за них, если строку нашли поиском. Поиск он звал 3099 раз
    против 3508 чтений: половина работы была нецитируемой по построению.
    """
    return [ev for ev in chain.by_kind("tool_output")  # type: ignore[arg-type]
            if getattr(ev, "obtained_via", "") == "find_in_files"]


def _search_names_file(evidences: list, cited_body: str):
    """Улика поиска, в чьей выдаче назван ЭТОТ файл, или None.

    Выдача поиска перечисляет попадания как «путь:строка: текст», значит
    путь, встретившийся в ней, — свидетельство об этом файле. Сверяется сам
    путь, а не подстрока: иначе ссылка на `core/x.py` цеплялась бы за
    упоминание `core/xyz.py`.
    """
    cited = str(cited_body).split(":", 1)[0].strip().replace("\\", "/").lower()
    if not cited:
        return None
    for ev in evidences:
        haystack = str(getattr(ev, "excerpt", "") or "").replace("\\", "/").lower()
        if cited in haystack:
            return ev
    return None


def _candidates_for(citation: Citation, chain: ProvenanceChain) -> list:
    """Улики, среди которых ищется эта ссылка.

    `runtime` — measured, not gathered: the process reading its own
    interpreter, version, pid and cwd. Its own execution is the proof, and
    until 2026-08-14 there was no channel through which it could be offered.

    `web` добирает попадания поиска в интернете, `file` — выдачу поиска по
    файлам: и то и другое свидетельства о предмете ссылки, добытые иначе,
    чем прямым открытием.
    """
    candidates = list(chain.by_kind(citation.expected_kind))  # type: ignore[arg-type]
    extra: list = []
    if citation.prefix == "runtime":
        extra = runtime_evidence_pool()
    elif citation.prefix == "web":
        extra = list(chain.by_kind("web_search_hit"))  # type: ignore[arg-type]
    elif citation.prefix == "file":
        extra = _search_output_evidence(chain)
    if not extra:
        return candidates
    seen = {ev.id for ev in candidates}
    return candidates + [ev for ev in extra if ev.id not in seen]


def match_citation(citation: Citation, chain: ProvenanceChain) -> Evidence | None:
    candidates = _candidates_for(citation, chain)
    if not candidates:
        return None
    if not citation.body:
        return candidates[0]
    body_lower = citation.body.lower()
    if citation.prefix == "file":
        # A file citation is compared AS A PATH: one file may be written
        # `x.txt`, `./x.txt` or with the workspace prefix, and those are the
        # same file. The window is decided first: when two reads of one file
        # are in the chain, `[file:x.py:200-215]` must land on lines 200-215,
        # not on whichever read came first.
        for ev in candidates:
            if same_file(citation.body, ev.source_id, same_window=True):
                return ev
        for ev in candidates:
            if same_file(citation.body, ev.source_id):
                return ev
        hit = _search_names_file(_search_output_evidence(chain), citation.body)
        if hit is not None:
            return hit
    for ev in candidates:
        # Every other prefix keeps the substring rule — a web query or a
        # memory id is a string, not a path.
        if citation.prefix == "file":
            continue
        if body_lower in ev.source_id.lower():
            return ev
    if citation.prefix in _NO_TOKEN_FALLBACK_PREFIXES:
        if citation.prefix == "web":
            body_lower_full = citation.body.lower()
            body_is_url = body_lower_full.startswith(("http://", "https://")) or "://" in body_lower_full
            if not body_is_url:
                search_only = [ev for ev in candidates if ev.kind == "web_search_hit"]
                if search_only:
                    body_tokens = _tokenise_citation_body(citation.body)
                    if body_tokens:
                        best: Evidence | None = None
                        best_score = 0
                        for ev in search_only:
                            sid_lower = ev.source_id.lower()
                            score = sum(1 for tok in body_tokens if tok in sid_lower)
                            if score > best_score:
                                best = ev
                                best_score = score
                        if best_score >= 1:
                            return best
        return None
    body_tokens = _tokenise_citation_body(citation.body)
    if not body_tokens:
        return None
    best: Evidence | None = None
    best_score = 0
    for ev in candidates:
        sid_lower = ev.source_id.lower()
        score = sum(1 for tok in body_tokens if tok in sid_lower)
        if score > best_score:
            best = ev
            best_score = score
    if best_score >= 1:
        return best
    return None


def best_run_of_source(ev: Evidence, chain: ProvenanceChain, claim: str) -> Evidence:
    """Из прогонов ОДНОГО источника — тот, что называет литералы утверждения.

    `match_citation` отдаёт первую улику с подходящим адресом. У инструмента
    адрес один на все вызовы (`tool_output:python_probe`), и цитата садилась на
    первый прогон: прогон драйвов 2026-09-19 — верное «a_f = 1.142e+26» из
    второго запуска python_probe получило [claim-refuted], за вечер 12 из 15
    опровержений были этим гейтом на выводах инструментов. Цитата называет
    источник, каждый его прогон в этом ходе — этот источник. Улики с ДРУГИМ
    адресом не участвуют: чужой текст значения не отмывает (MIR-060).
    """
    from .verifier_absence import literals_absent_from_excerpt

    runs = [e for e in chain.evidences if e.source_id == ev.source_id and e.kind == ev.kind]
    if len(runs) < 2:
        return ev
    return min(runs, key=lambda e: len(literals_absent_from_excerpt(claim, e.excerpt or "", e.source_id or "")))


def _semantic_nli_check(claim: str, excerpt: str, llm: Any, *, max_chars: int = _MAX_EXCERPT_FOR_NLI) -> bool:
    try:
        prompt = f"Source excerpt:\n{excerpt[:max_chars]}\n\nClaim: {claim[:300]}\n\nDoes the source excerpt support the claim? Answer yes or no."
        answer = llm.complete(system=_NLI_SYSTEM, user=prompt, max_tokens=4, temperature=0.0)
        return answer.strip().lower().startswith("yes")
    # FAIL CLOSED, deliberately: this is the NLI support check, and an
    # unreachable model must never be read as "the source supports the
    # claim". `False` means unsupported, which is the safe verdict —
    # the opposite default would launder unverified claims on any outage.
    except Exception:  # noqa: BLE001
        return False


def _find_semantic_support(claim: str, chain: ProvenanceChain, llm: Any) -> Evidence | None:
    candidates = sorted(chain.evidences, key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        if _semantic_nli_check(claim, ev.excerpt, llm):
            return ev
    return None


def _find_structured_support(claim: str, chain: ProvenanceChain) -> Evidence | None:
    from core.structured_facts import claim_supported_by, extract_facts
    candidates = [ev for ev in chain.evidences if ev.kind == "tool_output"]
    candidates.sort(key=lambda e: e.confidence, reverse=True)
    for ev in candidates:
        if not ev.excerpt:
            continue
        facts = extract_facts(ev.excerpt)
        if facts.is_empty():
            continue
        if claim_supported_by(claim, facts):
            return ev
    return None


def _tool_citation_for(ev: Evidence) -> str:
    sid = ev.source_id or ""
    body = sid[len("tool_output:"):] if sid.startswith("tool_output:") else sid or "structured"
    return f"[verified:tool:{body}]"


def _is_derivative_subagent_evidence(ev: Evidence) -> bool:
    """Is this evidence merely a sub-agent's own synthesis (a witness)?

    The parent loop embeds only a sub-agent's *answer text* (via
    ``SubAgentRunResult.to_evidence_text()``) into its provenance chain —
    never the sub-agent's raw file / web evidences. So the parent can never
    independently inspect what a sub-agent looked at; it holds only the
    child's prose conclusion.
    """
    excerpt = ev.excerpt or ""
    if _SUBAGENT_META_RE.search(excerpt) is not None:
        return True
    sid = ev.source_id or ""
    return bool("subagent_" in sid or sid == "tool_output:spawn_subagent")




# ── R2/R3 (2026-08-13, probe_r1) ──────────────────────────────────────────────

_COUNT_WORDS: dict[str, int] = {
    "один": 1, "одна": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


#: Пункт, который предложение само же отвергло, — не перечислен, а исключён.
_ENUM_EXCLUDED_RE = re.compile(
    r"не\s+подход|не\s+подошл|не\s+входит|исключ|not\s+match|excluded",
    re.IGNORECASE,
)
#: Пункт, который целиком — слово-связка, а не член перечисления: «(например,
#: яблоко, груша)» перечисляет два плода, а не три. Замер 2026-09-23: в живом
#: ответе «(например, планировщик говорит…, а верификатор…)» связка ушла в счёт.
_DISCOURSE_ONLY_RE = re.compile(
    r"^(?:например|в\s+частности|то\s+есть|скажем|в\s+том\s+числе|а\s+именно|"
    r"e\.g\.?|i\.e\.?|for\s+example|for\s+instance|namely)$",
    re.IGNORECASE,
)
#: Группа цифр внутри дроби: «0.6», «0,6», «1.5». Десятичное число — один
#: токен (так режут числа токенизаторы, начиная с Penn Treebank); его дробная
#: часть счётом не бывает.
#: Перечень, который сам объявил себя неполным: «(Tong, Carroll, Preskill и
#: др.)» не может опровергнуть «41 файл». Замер 2026-09-24 по 441 живому
#: ответу: клеймо claim-refuted стояло на «лежит 41 текстовый файл с лекциями
#: (Tong, Carroll, Srednicki, Preskill и др.)» — верном утверждении.
_PARTIAL_LIST_RE = re.compile(
    r"(?:\bи\s+(?:др|пр)\.?|\bи\s+т\.?\s*(?:д|п)\.?|\bи\s+так\s+далее|\bи\s+прочие|"
    r"\betc\.?|\band\s+so\s+on|\band\s+others|…|\.\.\.)\s*$",
    re.IGNORECASE,
)
_DECIMAL_PART_BEFORE_RE = re.compile(r"\d[.,]$")
_DECIMAL_PART_AFTER_RE = re.compile(r"^[.,]\d")


#: Число, за которым идёт месяц или год, или перед которым стоит тире
#: диапазона, — ДАТА, а не заявленный счёт. Рабочий заказ 1, проход 2
#: (2026-09-05): «…на 15–25 сентября 2026 (1 взрослый, эконом, только ручная
#: кладь)…» дал count_mismatch expected=25 actual=3 — верный вывод «источники
#: заблокированы» был опровергнут датой вылета.
_DATE_LIKE_NUMBER_RE = re.compile(
    r"(?:[–—-]\s*)?\b(\d{1,2})\s+(?:"
    r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
    r"\b\d{1,2}\s+\d{4}\b|\b\d{1,2}[.:]\d{2}\b",
    re.IGNORECASE,
)


def _number_is_a_date(text: str, m: Any) -> bool:
    """The matched number sits in a date: a month follows, a range dash
    precedes, or a year / clock time follows."""
    start = max(0, m.start() - 3)
    window = text[start:m.start() + 24]
    if _DATE_LIKE_NUMBER_RE.search(window):
        return True
    before = text[max(0, m.start() - 3):m.start()]
    return bool(re.search(r"[–—-]\s*$", before))


#: Скобка-пояснение («по данным поиска — …», «из них …», «всего …») — не
#: перечень предметов. 2026-09-21: «встречается 5 раз … (по данным поиска — 10
#: совпадений, из них 5 строк с X и 5 с Y)» — скобка прочитана как перечень из
#: двух, и ВЕРНОЕ «5 и 5» получило клеймо лжи. Перечень с количествами
#: («rotor-33: 5 шт., gasket-9: 0 шт.») остаётся перечнем.
_ITEM_CARRIES_A_COUNT_RE = re.compile(
    r"(?i)^(?:по\s+данным|согласно|всего|итого|according|in\s+total)|\bиз\s+них\b|\bof\s+which\b")


#: Скобка относится к БЛИЖАЙШЕМУ числу перед ней. Замер 2026-09-22 по всем
#: ответам мостика: 4 срабатывания, все 4 ложные — «4 константы, 2
#: dataclass-модели (SplitStep, SplitPlan)» сверялось с «4»; «строка 58
#: определяет … (…)» приняло номер строки за счёт; «шесть записей, из них три
#: … (одна medium, две без значения)» — разбивку по количествам за перечень;
#: «три случая (вставка `Edit[i, j−1]+1`, …)» резало пункты по запятым ВНУТРИ
#: кода и насчитало шесть.
_PAREN_RE = re.compile(r"\(([^()]{2,200})\)")
_COUNT_TOKEN_RE = re.compile(
    r"\b(\d{1,2})\b|\b(" + "|".join(_COUNT_WORDS) + r")\b", re.IGNORECASE)
_NOUN_THEN_GAP_RE = re.compile(r"\s+[\wЀ-ӿ-]+[^().\n]{0,60}$")
#: Число-адрес (строка, пункт, шаг, глава), а не количество.
_ADDRESS_BEFORE_RE = re.compile(
    r"(?i)(?:строк\w*|line|lines|стр\.|пункт\w*|№|#|§|глав\w*|раздел\w*|"
    r"chapter|section|step|шаг\w*)\s*$")
#: Пункт, который начинается с количества, — разбивка, а не предмет перечня.
_ITEM_STARTS_WITH_A_COUNT_RE = re.compile(
    r"(?i)^(?:\d+|" + "|".join(_COUNT_WORDS) + r")\b")
_OPENERS, _CLOSERS = "([{«", ")]}»"


def _top_level_items(inner: str) -> list[str]:
    """Пункты скобки: деление по `,`/`;` только вне кода, скобок и кавычек."""
    items, buf, depth, in_code = [], [], 0, False
    for ch in inner:
        if ch == "`":
            in_code = not in_code
        elif not in_code and ch in _OPENERS:
            depth += 1
        elif not in_code and ch in _CLOSERS and depth:
            depth -= 1
        elif ch in ",;" and not in_code and depth == 0:
            items.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    items.append("".join(buf).strip())
    return [item for item in items if item]


def _is_decimal_part(text: str, token: re.Match[str]) -> bool:
    """Группа цифр — часть десятичного числа («0.6», «0,6»), а не счёт."""
    if not token.group(1):
        return False
    return bool(_DECIMAL_PART_BEFORE_RE.search(text[:token.start()])
                or _DECIMAL_PART_AFTER_RE.match(text[token.end():]))


def _claimed_count_before(text: str, paren_start: int) -> int:
    """Счёт, к которому относится скобка: ближайшее число перед ней, за
    которым идёт существительное; 0 — такого нет (дата, адрес, не счёт)."""
    lead_from = max(0, paren_start - 120)
    # Точка режет предложение, но не дробь: замер 2026-09-23 — в «падает на
    # 0.6 за каждое противоречие (…)» точка разрезала 0.6, и шестёрка стала
    # «обещанными шестью пунктами»; ИСТИННОЕ утверждение ушло человеку с
    # клеймом claim-refuted.
    lead = re.split(r"(?<!\d)\.|\.(?!\d)|[\n()]", text[lead_from:paren_start])[-1]
    offset = paren_start - len(lead)
    # Ближайшее число, за которым идёт существительное: в «три позиции с qty
    # меньше 6 (…)» шестёрка — порог сравнения, счёт — «три».
    token = next((t for t in reversed(list(_COUNT_TOKEN_RE.finditer(lead)))
                  if _NOUN_THEN_GAP_RE.match(lead[t.end():])
                  and not _is_decimal_part(lead, t)), None)
    if token is None:
        return 0
    at = _COUNT_TOKEN_RE.match(text, offset + token.start())
    if at is None or (at.group(1) and _number_is_a_date(text, at)):
        return 0
    if _ADDRESS_BEFORE_RE.search(text[:offset + token.start()]):
        return 0
    return int(token.group(1)) if token.group(1) else _COUNT_WORDS.get(token.group(2).lower(), 0)


def enumeration_count_reason(text: str) -> Any:
    """R2: заявленный счёт против СОБСТВЕННОГО перечисления того же
    предложения.
    """
    from .verifier_models import ClaimReason

    text = text or ""
    for paren in _PAREN_RE.finditer(text):
        claimed = _claimed_count_before(text, paren.start())
        if claimed < 2:
            continue
        if _PARTIAL_LIST_RE.search(paren.group(1).strip()):
            continue
        items = _top_level_items(paren.group(1))
        if len(items) < 2 or any(
                _ITEM_CARRIES_A_COUNT_RE.search(item) or _ITEM_STARTS_WITH_A_COUNT_RE.search(item)
                for item in items):
            continue
        counted = sum(1 for item in items
                      if not _ENUM_EXCLUDED_RE.search(item)
                      and not _DISCOURSE_ONLY_RE.match(item.strip(" \t,;:—-")))
        if counted != claimed:
            return ClaimReason(
                code="count_mismatch",
                expected=str(claimed),
                actual=str(counted),
                explanation=(
                    "заявленный счёт противоречит собственному перечислению "
                    "в том же предложении"
                ),
                computed_from="собственное перечисление куска",
            )
    return None


def literal_covered_by_union(
    expected: str,
    evidences: list[Any],
    chain_source_ids: list[str] | None = None,
) -> bool:
    """R3: литерал накрыт ОБЪЕДИНЕНИЕМ процитированных улик (текст + адрес)."""
    # `expected` — склейка до трёх литералов через ", " (см. absent_literal_
    # reason): живой прогон a5813910 показал, что поиск склейки как одной
    # подстроки не находил НИЧЕГО, и объединение не снимало ни одного иска.
    needles = [n.strip().lower() for n in (expected or "").split(",") if n.strip()]
    if not needles:
        return False
    haystacks = [
        (getattr(ev, "excerpt", "") or "").lower()
        + "\n" + (getattr(ev, "source_id", "") or "").lower()
        for ev in evidences or []
    ]
    # Живой 0d88ba79: кусок цитировал ОДИН файл, называя другие, прочитанные
    # тем же ходом. Литерал, совпадающий с АДРЕСОМ улики цепи, не выдуман —
    # его референт открывался. Только адреса, не тексты: содержимое чужой
    # улики отмывало бы значения обратно (MIR-060).
    haystacks.extend((sid or "").lower() for sid in chain_source_ids or [])
    return all(any(n in h for h in haystacks) for n in needles)
