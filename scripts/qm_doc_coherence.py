"""Experimental check of quantum lab notes for contradictory statuses (R1) and counts (R2)."""
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
    """Sentences over paragraphs, not lines: a count and its enumeration may straddle a line."""
    out: list[tuple[int, str]] = []
    for lineno, para in _paragraphs(text):
        for part in re.split(r"(?<=[.;:])\s+", para):
            if part.strip():
                out.append((lineno, part.strip()))
    return out


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """Whitespace-normalised paragraphs with their true starting line."""
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
    """Blank out fenced blocks and inline code spans (quoted, not asserted); keeps line numbers."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = re.sub(r"```.*?```", blank, text, flags=re.DOTALL)
    # Inline spans may wrap across a newline, so the pattern allows newlines inside.
    return re.sub(r"`[^`]*`", blank, text)


def check(path: Path) -> list[str]:
    """Return R1/R2 findings for one Markdown file."""
    text = _strip_quotations(path.read_text(encoding="utf-8"))
    findings: list[str] = []

    # R1 -- status coherence per subject
    seen: dict[str, dict[str, list[tuple[int, str]]]] = {}
    # Rosters ("candidates: **0** (file-centric), **2** (current)") are read per paragraph:
    # the word "candidate" and the bolded numerals sit in different sentences.
    for lineno, para in _paragraphs(text):
        if "candidate" not in para.lower():
            continue
        for num, label in ROSTER.findall(para):
            lab = label.lower()
            for cls, vocab in (("ALIVE", ALIVE), ("DEAD", DEAD)):
                if any(w in lab for w in vocab):
                    seen.setdefault(f"Candidate {num}", {}).setdefault(
                        cls, []).append((lineno, label))

    # A heading that names a subject scopes the lines under it until the next heading.
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
    """Check files (default: SUBJECT_MODEL.md); exit 0 clean, 1 incoherent, 2 unreadable."""
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
