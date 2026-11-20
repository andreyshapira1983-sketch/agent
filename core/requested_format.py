"""A human-requested answer shape is the deliverable: honor requested last-line formats (e.g. "FINAL ANSWER: ...") by finding the value, converting it in a separate step if needed, and putting the line last and clean, with a caveat line above it when the value is unverified."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.warning_words import WARNING_KINDS

#: "LABEL: [FILLER]" or "LABEL: <filler>": label in capitals, filler in [] in capitals, in <> up to five words without digits; narrow on purpose ("ERROR: [Errno 2]" from a pasted log is no template).
_TEMPLATE_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])([A-ZА-ЯЁ][A-ZА-ЯЁ0-9 _-]{1,40}?)\s*[:：]\s*"
    r"(?:\[[A-ZА-ЯЁ][A-ZА-ЯЁ _-]*\]|<[^<>\d]{1,60}>)"
)

_CITATION_KINDS = (
    "verified:", "declared:", "general-knowledge", "web:", "file:", "file_read:", "file_write:",
    "search:", "test:", "log:", "shell:", "tool:", "diff:", "memory:", "sensor:", "runtime:",
    "dialogue:", "artifact:", "prior_turn:", "user",
)
_TAG_RE = re.compile(
    r"\s*\[(?:" + "|".join(re.escape(k) for k in (*WARNING_KINDS, *_CITATION_KINDS)) + r")[^\]]*\]",
    re.IGNORECASE,
)
_WARNING_RE = re.compile(r"\[(?:" + "|".join(re.escape(k) for k in WARNING_KINDS) + r")", re.IGNORECASE)

MISSING_PREFIX = "⚠️ Заданную строку "
UNCONFIRMED_PREFIX = "⚠️ Значение заданной строки "
_VALUE_LINE_RE = re.compile(r"^[A-ZА-ЯЁ][A-ZА-ЯЁ0-9 _-]{1,40}[:：]\s*\S")


def is_tail_line(line: str) -> bool:
    """Tail line of `honor`, so human printing does not drop it."""
    return line.startswith((MISSING_PREFIX, UNCONFIRMED_PREFIX)) or bool(_VALUE_LINE_RE.match(line))


CONVERT_SYSTEM = (
    "You copy an answer into a required line. You get the user's request and the answer text. "
    "Reply with exactly one line: the required label, a colon, and the value the ANSWER TEXT "
    "gives. Follow the request's own rules for how the value is written. Never compute, guess "
    "or add anything the answer text does not say. If the answer text gives no value, reply "
    "with the single word NONE."
)


def requested_labels(question: str) -> tuple[str, ...]:
    """Labels of the lines the human required in the answer, in order of appearance."""
    out: list[str] = []
    for match in _TEMPLATE_RE.finditer(question or ""):
        label = " ".join(match.group(1).split())
        if sum(ch.isalpha() for ch in label) >= 2 and label not in out:
            out.append(label)
    return tuple(out)


def _line_re(label: str) -> re.Pattern[str]:
    # "Conclusion: FINAL ANSWER: 42": a line inside a contract header; the header stays (`_drop_line`), only the requested line goes.
    return re.compile(
        r"^(?P<head>[\s>*_`#-]*(?:conclusion\s*:)?[\s*_`]*)"
        + r"\s+".join(map(re.escape, label.split())) + r"[*_`]*\s*[:：]\s*(?P<value>.*)$",
        re.IGNORECASE | re.MULTILINE,
    )


def _drop_line(match: re.Match[str]) -> str:
    head = match.group("head")
    return head.strip() if "conclusion" in head.lower() else ""


def _clean(value: str) -> str:
    value = _TAG_RE.sub("", value)
    return value.strip().strip("*_`").strip()


def _last_value(text: str, label: str) -> tuple[str, bool] | None:
    """Value of the last "label: ..." line and whether it carried a warning label."""
    found = list(_line_re(label).finditer(text or ""))
    for match in reversed(found):
        value = _clean(match.group("value"))
        if value:
            return value, bool(_WARNING_RE.search(match.group("value")))
    return None


@dataclass
class Honored:
    text: str
    #: Where the line came from: answer | draft | converted | missing (for the journal).
    origins: dict[str, str] = field(default_factory=dict)


def honor(
    answer: str,
    *,
    question: str,
    draft: str = "",
    convert: Callable[[str, str], str] | None = None,
) -> Honored:
    """Requested lines last, clean, with a caveat above them if needed. Value search order: final answer, synthesis draft (gates may have cut it), one separate-step conversion (`convert(label, text)`); the line is never invented, a missing value is said in words."""
    labels = requested_labels(question)
    if not labels or not answer:
        return Honored(answer)
    body, tail, origins = answer, [], {}
    for label in labels:
        got, origin = _last_value(answer, label), "answer"
        if got is None:
            got, origin = _last_value(draft, label), "draft"
            got = (got[0], True) if got else None  # the gate cut it: unverified
        if got is None and convert is not None:
            origin = "converted"
            try:
                raw = convert(label, draft or answer)
            except Exception:  # noqa: BLE001 — conversion never fails the answer; the missing line is stated below
                raw = ""
            got = _last_value(raw, label) or ((_clean(raw), True) if raw.strip() else None)
            if got and (not got[0] or got[0].upper() == "NONE"):
                got = None
        if got is None:
            origins[label] = "missing"
            # No colon after the label, or the caveat itself would read as an answer line.
            tail.append(f"{MISSING_PREFIX}«{label}» заполнить нечем: в ответе нет её значения.")
            continue
        origins[label] = origin
        body = _line_re(label).sub(_drop_line, body)
        # Conversion only rewrites what the answer said; its marks stay in the body.
        if got[1]:
            tail.append(f"{UNCONFIRMED_PREFIX}«{label}» не подтверждено источником этого хода.")
        tail.append(f"{label}: {got[0]}")
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip()
    return Honored(body + "\n\n" + "\n".join(tail), origins)


def llm_converter(llm: object, question: str) -> Callable[[str, str], str] | None:
    """`convert` for `honor` over a model with `.complete(system=, user=, ...)`."""
    complete = getattr(llm, "complete", None)
    if complete is None:
        return None

    def convert(label: str, text: str) -> str:
        user = (f"REQUIRED LINE: {label}: <value>\n\nREQUEST:\n{question[-3000:]}\n\n"
                f"ANSWER TEXT:\n{text[-6000:]}")
        return str(complete(system=CONVERT_SYSTEM, user=user, max_tokens=200, temperature=0.0))

    return convert
