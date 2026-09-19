"""EXPERIMENTAL coherence probe for the quantum lab notes. Not production.

The question it answers is narrow: does a document assert two incompatible
things about one subject, or state a count that its own enumeration contradicts?
It is NOT told which sentence is stale. It has no authoritative copy to compare
against. It finds disagreement structurally, from the text alone, which is the
only version of the check that is evidence rather than bookkeeping.

Two rules, because the lab produced exactly two specimens of the disease:

  R1 STATUS COHERENCE   one subject carrying both an alive-class and a
                        dead-class status word.
  R2 COUNT COHERENCE    a spelled-out number introducing an enumeration whose
                        expanded membership has a different size.

Exit 0 clean, 1 if any incoherence is found, 2 if a file cannot be read.
Usage:  python scripts/qm_doc_coherence.py [file.md ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ALIVE = {"current", "surviving", "survives", "survived", "blocked on", "still alive"}
DEAD = {"falsified", "structurally insufficient", "dead", "killed", "refuted",
        "withdrawn", "superseded"}

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}

SUBJECT = re.compile(r"\bcandidate\s+(\d)\b", re.IGNORECASE)
ROSTER = re.compile(r"\*\*(\d)\*\*\s*\(([^)]+)\)")
COUNT_ENUM = re.compile(
    r"\b(" + "|".join(WORD_NUM) + r")\s+\w+[^.(]*\(([^)]*\b[A-Z]\d[^)]*)\)", re.IGNORECASE)


def _sentences(text: str) -> list[tuple[int, str]]:
    """Sentences over PARAGRAPHS, not lines.

    The first version of this probe worked line by line and found nothing. The
    diagnosis is the finding: in the specimen document the count `Seven
    constraints` sat at the end of one line and its enumeration `(E1, E3-E7)` at
    the start of the next, so a line-scoped check could not see them together.
    Derived assertions are not textually adjacent to what they derive from --
    which is precisely why this class of staleness survives proofreading.
    """
    out: list[tuple[int, str]] = []
    for lineno, para in _paragraphs(text):
        for part in re.split(r"(?<=[.;:])\s+", para):
            if part.strip():
                out.append((lineno, part.strip()))
    return out


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Whitespace-normalised paragraphs with their true starting line.

    Rosters are read at paragraph granularity on purpose. Sentence splitting cut
    `There are exactly three candidates:` away from `**2** (current)`, so the
    subject word and the status label ended up in different fragments and the
    named specimen stayed invisible even after the probe started biting
    elsewhere. Recorded because it is the same disease one level down: the
    detector's own scope was narrower than the fact it had to see.
    """
    out: list[tuple[int, str]] = []
    lineno = 1
    for para in re.split(r"(\n\s*\n)", text):
        if re.fullmatch(r"\n\s*\n", para):
            lineno += para.count("\n")
            continue
        joined = " ".join(para.split())
        if joined:
            out.append((lineno, joined))
        lineno += para.count("\n")
    return out


def _expand(enum: str) -> set[str]:
    """`E1, E3-E7` -> {E1,E3,E4,E5,E6,E7}. Ranges are inclusive."""
    members: set[str] = set()
    for chunk in re.split(r"[,;]", enum):
        chunk = chunk.strip()
        rng = re.fullmatch(r"([A-Z])(\d+)\s*[-–—]\s*(?:[A-Z])?(\d+)", chunk)
        if rng:
            letter, lo, hi = rng.group(1), int(rng.group(2)), int(rng.group(3))
            members.update(f"{letter}{n}" for n in range(lo, hi + 1))
            continue
        single = re.fullmatch(r"([A-Z]\d+)", chunk)
        if single:
            members.add(single.group(1))
    return members


def _strip_quotations(text: str) -> str:
    """Blank out fenced blocks and inline code spans -- MENTION, not USE.

    Found the hard way: the note's own specimen section quotes the two
    contradictions verbatim as evidence, and the probe lit up on the quotations.
    A structural detector has no notion of use versus mention, so the document
    has to mark it. Backticks are that mark, and blanking rather than deleting
    keeps every line number intact.
    """
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = re.sub(r"```.*?```", blank, text, flags=re.DOTALL)
    # Inline spans may WRAP: prose reflows and a quotation ends up straddling a
    # newline. The first version forbade newlines inside a span, so a wrapped
    # quotation stayed live and the probe fired on its own evidence a second
    # time -- the same use/mention failure, surviving one fix because the fix
    # assumed quotations do not wrap.
    return re.sub(r"`[^`]*`", blank, text)


def check(path: Path) -> list[str]:
    text = _strip_quotations(path.read_text(encoding="utf-8"))
    findings: list[str] = []

    # R1 -- status coherence per subject
    seen: dict[str, dict[str, list[tuple[int, str]]]] = {}
    # A roster names several subjects at once, each with its status in
    # parentheses: "candidates: **0** (file-centric), **2** (current)". Read at
    # PARAGRAPH granularity -- the word "candidate" and the bolded numerals sit
    # in different sentences.
    for lineno, para in _paragraphs(text):
        if "candidate" not in para.lower():
            continue
        for num, label in ROSTER.findall(para):
            lab = label.lower()
            for cls, vocab in (("ALIVE", ALIVE), ("DEAD", DEAD)):
                if any(w in lab for w in vocab):
                    seen.setdefault(f"Candidate {num}", {}).setdefault(
                        cls, []).append((lineno, label))

    # A heading naming a subject SCOPES the prose under it until the next heading
    # of the same level. Without this the probe's recall was limited to status
    # words sharing a sentence with the subject's name, and the bite test proved
    # it: an injected "Candidate 1 is the current surviving model" raised nothing,
    # because candidate 1's death is asserted in a sentence that does not repeat
    # its name. A status inherited from a heading was invisible.
    section: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        head = re.match(r"^(#{2,3})\s+(.*)$", line)
        if head:
            m = SUBJECT.search(head.group(2))
            section = f"Candidate {m.group(1)}" if m else None
            line = head.group(2)
        target = section
        m = SUBJECT.search(line)
        if m:
            target = f"Candidate {m.group(1)}"
        if not target:
            continue
        low = line.lower()
        for cls, vocab in (("ALIVE", ALIVE), ("DEAD", DEAD)):
            for word in vocab:
                if word in low:
                    seen.setdefault(target, {}).setdefault(cls, []).append((lineno, word))
    for subject, classes in sorted(seen.items()):
        if len(classes) > 1:
            where = "; ".join(
                f"{cls} at line {classes[cls][0][0]} ({classes[cls][0][1]!r})"
                for cls in sorted(classes)
            )
            findings.append(f"R1 {path.name}: {subject} carries both statuses -- {where}")

    # R2 -- a count against its own enumeration
    for lineno, sent in _sentences(text):
        for m in COUNT_ENUM.finditer(sent):
            stated = WORD_NUM[m.group(1).lower()]
            members = _expand(m.group(2))
            if members and stated != len(members):
                findings.append(
                    f"R2 {path.name}:{lineno}: says {m.group(1)!r} ({stated}) but "
                    f"({m.group(2)}) expands to {len(members)}: {sorted(members)}")
    return findings


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [
        Path("knowledge/quantum/SUBJECT_MODEL.md")]
    all_findings: list[str] = []
    for path in targets:
        if not path.is_file():
            print(f"UNREADABLE: {path}")
            return 2
        all_findings += check(path)
    for f in all_findings:
        print(f)
    print(f"\n{'INCOHERENT' if all_findings else 'COHERENT'}: {len(all_findings)} finding(s)")
    return 1 if all_findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
