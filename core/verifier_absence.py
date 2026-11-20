"""Certifying a claim of ABSENCE — the half of verification a citation cannot do."""
from __future__ import annotations

import re
from typing import Any

from core.evidence import Evidence

#: Distinctive literals: identifiers, SHAs, paths, version triplets (`3.11.9`); bare and decimal numbers are judged by `evaluate_claim_arithmetic`.
#: Hyphenated only with a digit inside (else prose like "read-only" would be an identifier); possessive quantifiers and {,160} caps keep parsing linear on hostile text.
_SALIENT_LITERAL_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]{0,160}+(?:_[A-Za-z0-9]{1,160}+)++"
    r"|[A-Za-z][A-Za-z0-9]{0,160}+"
    r"(?:-[A-Za-z0-9]{0,160}\d[A-Za-z0-9]{0,160}+)++(?:-[A-Za-z0-9]{1,160}+)*+"
    r"|[0-9a-f]{7,40}"
    r"|\d+(?:\.\d+){2,}"
    r"|[\w./\-]{1,160}\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml))\b"
)


#: Citation marker in the answer text: `[file:core/loop.py]`, `[memory:mem_...]`.
_CITATION_TOKEN_RE = re.compile(r"\[[^\[\]]*\]")


def salient_literals(text: str) -> set[str]:
    """Distinctive literals of the text, lower-cased."""
    return {m.group(0).lower() for m in _SALIENT_LITERAL_RE.finditer(text or "")}


def literals_absent_from_excerpt(
    claim: str, excerpt: str, source_id: str = ""
) -> set[str]:
    """Literals of the claim that are NOT in the cited evidence."""
    if not claim or not excerpt:
        return set()
    # a citation marker is a source address, not a claim about the world
    body = _CITATION_TOKEN_RE.sub(" ", claim)
    # the source's own name is not a foreign literal
    known = salient_literals(excerpt) | salient_literals(source_id or "")
    haystack = f"{excerpt}\n{source_id}".lower()
    return {
        lit for lit in salient_literals(body)
        if not _literal_covered(lit, known, haystack)
    }


_VERSION_RE = re.compile(r"\d+(?:\.\d+){2,}")


def _literal_covered(lit: str, known: set[str], haystack: str) -> bool:
    """Whether the literal is in the evidence; a version only as a whole ("3.15.2" is not inside "3.15.20")."""
    if _VERSION_RE.fullmatch(lit):
        return re.search(rf"(?<![\d.]){re.escape(lit)}(?!\w|\.\d)", haystack) is not None
    return lit in known or any(lit in k or k in lit for k in known)


def excerpt_is_cut(excerpt: str) -> bool:
    """Whether the excerpt is cut: by the budget (R8) or when the evidence was created (evidence._truncate)."""
    return ("[INTENT-BUDGET:" in excerpt or "[TOTAL-BUDGET:" in excerpt
            or excerpt.rstrip().endswith("...[truncated]"))


def absent_literal_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Demotion reason if the claim names what is not in the evidence."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    # an absence claim is judged by the fifth gate under the inverse rule
    if asserts_absence(chunk_text):
        return None
    absent = literals_absent_from_excerpt(
        chunk_text, ev.excerpt or "", ev.source_id or ""
    )
    if not absent:
        return None
    from .verifier_models import ClaimReason
    # a path other than the cited source is attribution (a lie about origin); otherwise only missing support (the excerpt may be about another place in the file)
    cited = (ev.source_id or "").split(":", 1)[-1].replace("\\", "/").lower()
    foreign = [
        lit for lit in absent
        if "/" in lit and "." in lit.rsplit("/", 1)[-1]
        and lit.replace("\\", "/").lower() not in cited
    ]
    # a number the evidence never printed is also a lie, not missing support
    if not foreign:
        foreign = [lit for lit in absent if any(ch.isdigit() for ch in lit)]
    # a truncated excerpt proves no absence and cannot confirm a literal beyond the cut: missing support, neither a lie nor "confirmed"
    if excerpt_is_cut(ev.excerpt or ""):
        foreign = []
    return ClaimReason(
        code="cited_literal_absent" if foreign else "cited_support_missing",
        expected=", ".join(sorted(absent)[:3]),
        actual="",
        explanation="утверждение называет то, чего нет в цитируемой улике",
        computed_from=ev.source_id or "",
    )


#: Minimum content words for a divergence from the evidence to mean anything ("Yes." is no subject).
_OFF_TOPIC_MIN_TOKENS = 3


def _dominant_script(text: str) -> str:
    """`cyrillic`, `latin` or `none`: which script dominates the text."""
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
    """Content words without bare numbers: a shared digit does not make a shared topic."""
    from .topic_tokens import discriminating_tokens

    # `discriminating_tokens` is the only one that applies the stop list ("the", "was")
    return {t for t in discriminating_tokens(text) if not t.isdigit()}


#: Words of meta-claims about the evidence itself, not the world ("both files agree").
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


#: Prefixes whose evidence is prose: against code and structured output a word comparison proves nothing and would pre-empt more precise gates.
_PROSE_PREFIXES: frozenset[str] = frozenset({"web", "search"})


def off_topic_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Demotion reason if the allowed citation is not about this claim (sixth gate).

    Demotes only on zero shared content words: a false refusal is costly and a paraphrase nearly always keeps one common word.
    """
    if prefix not in _PROSE_PREFIXES:
        return None
    excerpt_raw = ev.excerpt or ""
    # a truncated excerpt is judged by its visible part: the gate only demotes, and a blind "confirmed" is worse
    if not excerpt_raw.strip():
        return None
    # no number requirement; different languages and meta-claims are cut off below
    claim_tokens = _subject_tokens(_CITATION_TOKEN_RE.sub(" ", chunk_text))
    # a piece about the evidence itself: no shared words proves nothing
    if claim_tokens and claim_tokens <= _META_VOCABULARY:
        return None
    # meta words are not subtracted: "record", "file" are also ordinary nouns
    if len(claim_tokens) < _OFF_TOPIC_MIN_TOKENS:
        return None
    # different scripts are not different subjects: the agent reads English and answers Russian
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    known = _subject_tokens(excerpt_raw) | _subject_tokens(ev.source_id or "")
    # no share threshold: a natural paraphrase shares as few words with the evidence as an unrelated topic
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


#: Meta-denial: phrases declaring the whole claim false, not one of its members ("not less than", "and not 30" are excluded).
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
    """Demotion reason if the claim declares its own evidence false (ninth gate); if the evidence carries the denial too, the claim only relays it."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    excerpt_raw = ev.excerpt or ""
    if not excerpt_raw.strip():
        return None
    # the denial lexicon is language-dependent: a denial in other-language evidence is invisible
    if _dominant_script(chunk_text) != _dominant_script(excerpt_raw):
        return None
    body = _CITATION_TOKEN_RE.sub(" ", chunk_text)
    if not _META_DENIAL_RE.search(body):
        return None
    if _META_DENIAL_RE.search(excerpt_raw):
        return None  # the evidence itself denies: the claim relays it
    # without a shared subject it is the off-topic gate's business
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


#: A number in the text; with an approximation ("about 20") it is not judged.
_BARE_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

#: A point between digits is a decimal separator, not a sentence end.
_SENTENCE_SPLIT = re.compile(r"(?<![0-9])[.!?]+(?![0-9])|" + chr(10) + "+")

_APPROXIMATION_RE = re.compile(
    r"(?:\bоколо\b|\bпримерно\b|\bприблизительно\b|\bпорядка\b|~"
    r"|\bapproximately\b|\babout\b|\broughly\b|\bcirca\b)",
    re.IGNORECASE,
)

#: Share of claim words in an evidence sentence at which the piece is a verbatim restatement.
_RESTATEMENT_OVERLAP = 0.8


def restated_number_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Demotion reason if the piece repeats an evidence sentence with a changed number (tenth gate).

    Not a presence rule: computed numbers and comparison bounds legitimately are absent from the evidence, so only a near-verbatim restatement is judged.
    """
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    excerpt_raw = ev.excerpt or ""
    if not excerpt_raw.strip():
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
        # the piece repeats this sentence: the difference can only be in numbers
        known = {m.group(0) for m in _BARE_NUMBER_RE.finditer(sentence)}
        missing = {n for n in claimed if not any(n == k or n in k for k in known)}
        if not missing:
            return None
        from .verifier_models import ClaimReason

        # the cut may have hidden a sentence with the named number: truncated evidence does not accuse, it only fails to confirm
        if excerpt_is_cut(excerpt_raw):
            return ClaimReason(
                code="cited_support_missing",
                expected=", ".join(sorted(known)[:3]) or "(в предложении нет чисел)",
                actual=", ".join(sorted(missing)[:3]),
                explanation="в видимой части усечённой улики стоит другое число",
                computed_from=ev.source_id or "",
            )
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


#: Absence claim ("no X", "not implemented"); the marker is searched across the whole piece.
_ABSENCE_ASSERTION_RE = re.compile(
    r"(?:\bотсутству\w*|\bне\s+(?:найден\w*|реализован\w*|существу\w*|"
    # verbs of a missing mechanism ("does not handle", "not provided")
    r"содерж\w*|определ\w*|предусмотр\w*|обрабатыва\w*|поддержива\w*|"
    r"учитыва\w*)|\bнет\b|\bни\s+одного\b"
    r"|\bno\s+(?:\S+\s+){1,3}(?:exists?|found|implemented)|\bnot\s+(?:implemented|"
    # the same verbs in English, and `no handling` without a closing verb
    r"found|present|defined|exist)|\bdoes\s+not\s+(?:exist|contain|define|"
    r"implement|handle|support|check)"
    r"|\bno\s+(?:handling|support|validation|check|mechanism)\b"
    r"|\babsent\b|\bmissing\b)",
    re.IGNORECASE,
)

#: A subject named explicitly in quotes or apostrophes; a prose subject is deliberately not judged.
_NAMED_SUBJECT_RE = re.compile(r"[`\"']([A-Za-z_][A-Za-z0-9_.]{2,})[`\"']")


#: Part of a sentence asserting its subject is present: not a subject of absence.
_PRESENCE_RE = re.compile(
    r"(?:(?<!не\s)\bнайден\w*|\bпрочита\w*|\bпрочё?л\w*|\bзавершил\w*|\bесть\b"
    r"|(?<!не\s)\bсуществу\w*|(?<!не\s)\bприсутству\w*|\bна\s+месте\b"
    r"|\bзаписан\w*|\bсоздан\w*|\bread\b|\bfound\b|\bexists?\b|\bis\s+present\b"
    r"|\bcompleted\b|\bfinished\b|\bwritten\b|\bcreated\b"
    # to call is to say it exists ("the client is called `LLM`")
    r"|(?<!не\s)\bназыва\w*|(?<!не\s)\bзов[её]т\w*|\bis\s+called\b|\bis\s+named\b)",
    re.IGNORECASE,
)
#: Boundaries of clauses in a compound sentence: comma, semicolon, dash.
_CLAUSE_SPLIT_RE = re.compile(r"[,;]|\s[—–]\s")
#: Place of search ("no errors in `stderr`", "in file x.txt"): the place exists, the searched thing is absent.
_SEARCH_PLACE_RE = re.compile(
    r"\b(?:в|во|по|in|inside|within|across)\s+"
    r"(?:(?:файле|файлу|файлам|файлах|книге|тексте|логе|журнале|(?:the\s+)?(?:file|book|log|text))\s+)?"
    r"(?:`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'"
    r"|[\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv))",
    re.IGNORECASE,
)


#: A list of findings after a finding word is not a subject of absence; ends at a period+space so a period inside `core/loader.py` does not cut it.
_FOUND_MATERIAL_RE = re.compile(
    r"(?:совпадени\w*|результат\w*|matches|match|hits|найден\w*|found)\b(?:(?!\.\s)[^;\n])*",
    re.IGNORECASE)


#: Shown material: a parenthesised list and guillemets; apostrophes and straight quotes are excluded (they name the subject itself).
_ENUMERATED_SPAN_RE = re.compile(r"\([^()\n]*\)|«[^»\n]*»")

#: Tail of an exception message up to the sentence end: the instrument's quote, not a subject.
_ERROR_TAIL_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b[^.;\n]*")


def absence_subjects(claim: str) -> set[str]:
    """Subjects explicitly named in an absence claim; not subjects: what is asserted present, places of search, and findings."""
    # enumerations are cut out before the clause split: a comma in parentheses breaks the list
    body = _ENUMERATED_SPAN_RE.sub(" ", _CITATION_TOKEN_RE.sub(" ", claim or ""))
    # bare literals come only from the clause with the negation; from neighbours only what is named explicitly and without an error-message tail
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


#: A foreign voice inside the claim: quotes and apostrophes, and an exception-message tail.
_QUOTED_SPAN_RE = re.compile(
    r"`[^`\n]*`"
    r"|«[^»\n]*»"
    r'|"[^"\n]*"'
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b[^.;\n]*",
)


def _own_voice(claim: str) -> str:
    """The claim text without quotes and error-message tails."""
    return _QUOTED_SPAN_RE.sub(" ", claim or "")


def asserts_absence(claim: str) -> bool:
    """Whether the piece asserts in its OWN voice that something is absent."""
    return bool(_ABSENCE_ASSERTION_RE.search(_own_voice(claim)))


def absence_refuted_by_excerpt(claim: str, excerpt: str) -> bool:
    """Whether the cited evidence refutes the absence claim."""
    if not claim or not excerpt or not asserts_absence(claim):
        return False
    body = excerpt.lower()
    return any(subject in body for subject in absence_subjects(claim))


def absence_reason(chunk_text: str, ev: Evidence, prefix: str) -> Any | None:
    """Demotion reason if the evidence contains what the claim did not find."""
    if prefix in {"user", "memory", "general-knowledge"}:
        return None
    from .verifier_utils import truth_excerpt

    outcome = truth_excerpt(ev.excerpt or "")
    if not absence_refuted_by_excerpt(chunk_text, outcome):
        return None
    # only the named place itself can refute an absence, not another source
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
#: A place: a path after "in" not farther than three words and not after "about" (there the path is the subject, not the place).
_PLACE_RE = re.compile(
    r"\b(?:в|во|in|inside|within)\s+"
    r"(?:(?!(?:про|о|об|about|for)\b)[^\s,;:`'\"«]+\s+){0,3}"
    r"[`'\"«]?([\w./\\-]+\.(?:py|md|json|jsonl|txt|yaml|yml|cmd|toml|csv|log))\b",
    re.IGNORECASE)


def _denied_places(claim: str) -> set[str]:
    """Files named as a PLACE in the clause where the negation stands."""
    body = _CITATION_TOKEN_RE.sub(" ", claim or "")
    return {
        m.group(1).replace("\\", "/").lower()
        for part in _CLAUSE_SPLIT_RE.split(body)
        if _ABSENCE_ASSERTION_RE.search(_own_voice(part))
        for m in _PLACE_RE.finditer(part)
    }


def _cited_path(source_id: str) -> str:
    """Path of the cited source without the kind prefix and line range."""
    rest = source_id.split(":", 1)[-1] if ":" in source_id else source_id
    found = _PLACE_PATH_RE.search(rest)
    return found.group(0).replace("\\", "/").lower() if found else ""


def absence_certifiable(claim: str, excerpt: str) -> bool:
    """Whether an absence claim can be CERTIFIED by an excerpt at all."""
    return not asserts_absence(claim)


#: Search report of nothing found (tools/find_in_files.py): the searched thing and the scope.
_SEARCH_NONE_RE = re.compile(
    r"no matches for (['\"])(?P<query>.+?)\1 in \d+ text files under (?P<scope>\S+)")


#: "No such resource" answer (tools/web_fetch.py, 404/410): code and URL.
_NOT_FOUND_RE = re.compile(r"^HTTP (?:404|410)\b[^:]*: (?P<url>\S+)")
#: URL parts that name the site structure, not a subject.
_GENERIC_SEGMENTS = frozenset({"json", "simple", "project", "projects", "pypi", "api", "v1", "v2",
                               "www", "http", "https", "index", "search", "package", "packages"})


def absence_certified_by_not_found(claim: str, evidences: list[Any]) -> bool:
    """Absence proven by a 404/410 from web_fetch on a URL naming the subject.

    Narrow: only if the path part naming the subject stands in the claim; an error page is a source only for "nothing at this address".
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
        # the site name does not name the subject: only the path is looked at
        path = m.group("url").split("://", 1)[-1].split("?", 1)[0].lower().partition("/")[2]
        segments = [s for s in re.split(r"[/.]", path) if len(s) >= 4 and s not in _GENERIC_SEGMENTS]
        if any(s in low for s in segments):
            return True
    return False


def absence_certified_by_search(claim: str, evidences: list[Any]) -> bool:
    """Absence proven by a full search: the searched thing and scope are named in the claim; a find_in_files report is not a truncated excerpt."""
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
