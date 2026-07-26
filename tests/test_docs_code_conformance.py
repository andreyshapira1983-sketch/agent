"""The documentation's code references must keep resolving.

Four guards already cover one axis each: `docs_link_check` (relative links),
`agent_anatomy_check` (the `core/` module index), `commands_map_check` (registry
↔ COMMANDS_MAP parity) and `registry_tally` (issue counts). None of them looks at
the code references embedded in *prose* — "``core/loop.py`` does X", "see
``cli/app.py:69``", "``:self-apply-run`` applies" — which is where most
documentation claims actually live.

`scripts/docs_code_conformance.py` checks those, and this test runs it, so the
conformance is a build gate rather than something re-verified by hand whenever
somebody wonders. Measured when it was introduced: 31 documents, 611 code paths,
138 line anchors, 299 command tokens — all resolving.

When this fails, the fix is one of three, and the script says which:
  * the document points at a file that no longer exists → update the document;
  * a line anchor drifted → update it, or declare the document historical in
    `_HISTORICAL_ANCHOR_DOCS` if its anchors are provenance for an old commit;
  * a documented `:command` is not in the registry → the command was renamed or
    removed, or the token is prose and belongs in `_NON_COMMAND_TOKENS`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "docs_code_conformance.py"


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        encoding="utf-8",
        errors="replace",
    )


def test_every_code_reference_in_the_docs_resolves():
    result = _run()
    assert result.returncode == 0, (
        "documentation drifted from the code:\n" + result.stdout + result.stderr
    )
    assert "every code reference resolves" in result.stdout


def test_the_checker_actually_checked_something():
    """Guard the guard: an extractor that finds nothing must not pass silently."""
    out = _run().stdout
    for marker in ("code paths referenced", "line anchors checked", ":command tokens"):
        assert marker in out, out

    def _count(label: str) -> int:
        """First integer reported for that label.

        Parsed with a regex rather than `split(":")` because one of the labels
        (`:command tokens`) contains a colon itself — the naive split reads the
        label instead of the number.
        """
        line = next(l for l in out.splitlines() if label in l)
        match = re.search(r":\s*(\d+)", line[line.index(label) + len(label):])
        assert match is not None, line
        return int(match.group(1))

    assert _count("documents scanned") >= 25
    assert _count("code paths referenced") >= 400
    assert _count(":command tokens") >= 200
