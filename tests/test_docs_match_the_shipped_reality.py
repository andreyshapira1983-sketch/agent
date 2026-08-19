"""Documentation may not promise what the repository does not ship.

Found by the pre-push audit (2026-08-19), on a real fresh clone: the first
command in the README fails, because the code's default provider is
`anthropic` (core/llm.py) while the docs claim `mock`, and a clone has no
`.env`. The same audit found the operations guide still advertising Docker
files that commit 7f73567 deleted. The existing conformance guard
(scripts/docs_code_conformance.py) only follows `.py` paths, so neither
class was caught. These three checks close exactly those holes and nothing
wider.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

#: Artifacts a reader could be told to run. Checked only when ABSENT — the
#: guard is "do not promise what is not here", not "never mention".
_DELETABLE_ARTIFACTS = ("Dockerfile", "compose.yaml", "docs/DOCKER.md")

#: Documents that INSTRUCT a reader. The history books are excluded by role,
#: not by convenience: CODE_NOTES.md and MISTAKE_NOTEBOOK.md exist to record
#: why things were removed, so naming a deleted file there is their job.
_HISTORY_BOOKS = frozenset({"CODE_NOTES.md", "MISTAKE_NOTEBOOK.md"})

_DOC_FILES = tuple(
    p for p in (*_REPO.glob("docs/*.md"), _REPO / "README.md")
    if p.name not in _HISTORY_BOOKS
)


def _actual_provider_default() -> str:
    """The default the CODE really uses, read from the running function."""
    import os

    from core.llm import _provider

    saved = os.environ.pop("AGENT_PROVIDER", None)
    try:
        return _provider()
    finally:
        if saved is not None:
            os.environ["AGENT_PROVIDER"] = saved


def test_the_documented_provider_default_matches_the_code() -> None:
    actual = _actual_provider_default()
    offenders: list[str] = []
    for path in (_REPO / "docs" / "CONFIGURATION.md", _REPO / "agent_tick.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "AGENT_PROVIDER" not in line or "default" not in line.lower():
                continue
            claimed = re.findall(r"`?\b(mock|openai|anthropic|huggingface|local)\b`?", line)
            if claimed and actual not in claimed:
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        f"docs claim a provider default the code does not use (real: {actual!r}):\n  "
        + "\n  ".join(offenders)
    )


def test_docs_do_not_promise_deleted_artifacts() -> None:
    missing = [a for a in _DELETABLE_ARTIFACTS if not (_REPO / a).exists()]
    offenders: list[str] = []
    for path in _DOC_FILES:
        if not path.is_file():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for artifact in missing:
                name = artifact.rsplit("/", 1)[-1]
                if name in line:
                    offenders.append(
                        f"{path.relative_to(_REPO).as_posix()}:{lineno} -> {name}")
    assert not offenders, (
        "documentation points at artifacts this repository does not ship:\n  "
        + "\n  ".join(offenders[:20])
    )


def test_the_quick_start_names_the_setup_it_needs() -> None:
    """A fresh clone runs the first block verbatim: it must install deps and
    create the offline .env, or it ends in a traceback."""
    readme = (_REPO / "README.md").read_text(encoding="utf-8", errors="replace")
    start = readme.find("## Quick start")
    assert start != -1, "README has no Quick start section"
    block = readme[start:start + 900]
    assert "requirements.txt" in block, "Quick start never installs dependencies"
    assert ".env" in block, (
        "Quick start never creates .env — with no keys the first command dies "
        "on the real default provider"
    )
