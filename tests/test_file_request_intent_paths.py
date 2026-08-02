"""Pin the path-mention parsers at their new home (loop decomposition, piece 3).

These two had no direct tests while they lived on ``AgentLoop`` — they were
covered only through the multi-file-review flows. The move gave them a public
seam, and the equivalence check run before deleting the originals caught a real
transcription error on exactly one shape: a backslash-separated path collapsed
to its basename. That case is pinned here permanently, because it is the one
that would have shipped broken.
"""
from __future__ import annotations

import random
import re
import string
import time

from core.file_request_intent import extract_path_mentions, normalize_path_mention

# The regex `extract_path_mentions` replaced, verbatim (formerly
# `AgentLoop._extract_path_mentions`, removed at commit 1b7dffa's successor).
# Kept here as the ORACLE: the scanner's contract is "exactly what this
# pattern matched", so equivalence is asserted forever rather than argued
# once. The pattern itself is quadratic — that is WHY it was replaced
# (CodeQL alert #11; measured 2.79 s on `"a." * 8000`) — so the oracle is
# only ever run on short inputs here.
_ORACLE_RE = re.compile(
    r"(?<![/.\-])"
    r"(?P<path>"
    r"(?:[A-Za-z]:[\\/])?"
    r"(?:/)?"
    r"(?:\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_.-]+[\\/])*"
    r"[A-Za-z0-9_.-]+\."
    r"(?:py|md|txt|json|yml|yaml|pdf)"
    r")",
    flags=re.IGNORECASE,
)


def _oracle(text: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for match in _ORACLE_RE.finditer(text):
        path = match.group("path").rstrip(".,;:!?)\"]}'")
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# extract_path_mentions
# ---------------------------------------------------------------------------

def test_finds_paths_in_first_mention_order():
    text = "review core/loop.py and docs/ROADMAP.md please"
    assert extract_path_mentions(text) == ["core/loop.py", "docs/ROADMAP.md"]


def test_backslash_separated_path_survives_whole():
    """The transcription-error case: must be the full path, not `file.md`."""
    text = "прочитай backslash\\style\\file.md"
    assert extract_path_mentions(text) == ["backslash\\style\\file.md"]


def test_case_duplicates_collapse_to_the_first_spelling():
    assert extract_path_mentions("core/loop.py core/LOOP.py") == ["core/loop.py"]


def test_trailing_sentence_punctuation_is_stripped():
    assert extract_path_mentions("see (docs/a.md), 'b.txt'!") == [
        "docs/a.md", "b.txt",
    ]


def test_unknown_extensions_are_not_paths():
    assert extract_path_mentions("run file.exe and config.toml") == []


def test_no_paths_means_empty_list():
    assert extract_path_mentions("никаких путей здесь нет") == []


def test_a_wall_of_dashes_is_part_of_the_path_and_completes():
    """Dashes are legal segment characters, so the wall IS the path's first
    segment — pinned so nobody "fixes" it into a split. The leading guard's
    job on this input is cost, not exclusion: the run completes instantly
    instead of rescanning quadratically (measured when the guard was added).
    """
    wall = "-" * 10_000
    assert extract_path_mentions(wall + "/a.py") == [wall + "/a.py"]


# ---------------------------------------------------------------------------
# Equivalence with the replaced regex — the scanner's whole contract
# ---------------------------------------------------------------------------

# Shapes that each broke a draft of the scanner before it reached this repo,
# every one found by the oracle rather than by reasoning. If one of these
# fails, the scanner has drifted from the pattern it claims to replace.
_TRICKY = [
    "a.py", "a.pyz", "a.py.mdz", "de.py/x",           # rightmost-extension rule
    "a//b.py", "//a.py", "a//b/c.py",                 # double-slash body break
    "xC:/a.py", "x.C:/a.py", "-C:/a.py", "aC:/x.py",  # drive after a failed run
    "C:/a.py", "C:\\a\\b.md", "C://a.py", "C:", "C:/",
    "https://www.py", "see https://example.com/paper.pdf",  # url tails
    ":/--ayp.py\\a/ba", "b\\/-.pyb--/bb\\paa\\p",     # slash after colon/backslash
    "/a\\-ypbpyp-.py", ".../a.py", "----/a.py",
    "./a.py", "../a.py", ".\\a.py", "..\\a.py", ".py", "..py",
    "a.b.c.yaml", "a.pdf.py", "a.pyf", "a.py/b.py/c.py", "a.pybC:/x.md",
    "a:b:c.py", "x/:y.md", "файл.py и π.py",
]


def test_scanner_matches_the_oracle_on_every_recorded_trap():
    for text in _TRICKY:
        assert extract_path_mentions(text) == _oracle(text), repr(text)


def test_scanner_matches_the_oracle_under_seeded_fuzz():
    """4 000 seeded random strings over path-shaped alphabets.

    The full verification before the swap ran 150 000 cases plus every file
    of this repository, all identical; this seeded subset keeps the claim
    enforced in CI at a cost the quadratic oracle can afford (inputs ≤ 60
    chars).
    """
    rng = random.Random(20260802)
    alphabets = [
        string.ascii_letters + string.digits + "./\\-_ .:()'\"!?,;`«»",
        "a./\\-: ",
        "ab.py/\\:-",
        "Cc:/\\a.pymdtxjsonl ",
    ]
    for alphabet in alphabets:
        for _ in range(1000):
            text = "".join(
                rng.choice(alphabet) for _ in range(rng.randint(0, 60))
            )
            assert extract_path_mentions(text) == _oracle(text), repr(text)


def test_adversarial_walls_stay_fast():
    """The reason the regex died: `"a." * 8000` cost 2.79 s there. The
    scanner must stay orders of magnitude under that; the bound is loose
    (0.5 s) so a slow CI box cannot flake it, while the quadratic it guards
    against blows through it by 5x.
    """
    for shape in ("a." * 8000, "a" * 16000, "-a" * 8000, "s://w" * 3000):
        start = time.perf_counter()
        extract_path_mentions(shape)
        assert time.perf_counter() - start < 0.5, shape[:20]


# ---------------------------------------------------------------------------
# normalize_path_mention
# ---------------------------------------------------------------------------

def test_normalize_unifies_separators_and_case():
    assert normalize_path_mention("Core/Loop.PY") == "core\\loop.py"


def test_normalize_strips_quotes_and_leading_dot_slash():
    assert normalize_path_mention("  './x.md'  ") == "x.md"


def test_normalized_forms_compare_equal_across_styles():
    assert normalize_path_mention("core/loop.py") == normalize_path_mention(
        ".\\core\\loop.py"
    )
