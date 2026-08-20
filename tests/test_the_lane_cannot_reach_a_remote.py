"""The lane's git front-end must refuse a remote verb, not merely lack a name.

Found 2026-08-20 auditing the suite as an instrument. Four separate tests —
test_safe_vcs.py:116, test_self_apply_lane.py:344,
test_self_apply_approval_bridge.py:408, test_self_build_producer.py:292 —
assert the same thing: `not hasattr(SafeVCS, "push"/"fetch"/"pull"/"remote")`.
Four copies of one claim are one witness, not four, and the claim they make is
about NAMES. `SafeVCS` carries a general git executor (`_git`, with an
injectable `runner`), so every forbidden verb was reachable through it — shown
live in a throwaway repository: `SafeVCS(workspace=d)._git("remote", "-v")`
returned 0 while all four name assertions stayed green.

That is not a report that the agent pushes code. Nothing calls `_git` with a
network verb today. It is a report that the property "cannot reach a remote"
rested on nobody choosing such a name, and that a rename would pass every
guard the suite has.

The invariant below is about capability: a verb outside the local set must be
refused BEFORE git is invoked. Hence the spy runner — a test that let the verb
run and checked for an error could not tell "refused" from "tried and failed".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.safe_vcs import SafeVCS, VcsError, VcsResult


def _spy() -> tuple[list[list[str]], object]:
    calls: list[list[str]] = []

    def runner(argv: list[str], *, cwd: Path) -> VcsResult:
        calls.append(list(argv))
        return VcsResult(0, "", "")

    return calls, runner


@pytest.mark.parametrize(
    "argv",
    [
        ("push", "origin", "HEAD"),
        ("fetch", "--all"),
        ("pull"),
        ("remote", "-v"),
        ("clone", "https://example.invalid/x.git"),
        ("merge", "other"),
    ],
)
def test_a_remote_verb_never_reaches_the_runner(workspace: Path, argv) -> None:
    calls, runner = _spy()
    vcs = SafeVCS(workspace=workspace, runner=runner)
    with pytest.raises(VcsError):
        vcs._git(*([argv] if isinstance(argv, str) else argv))
    assert calls == [], f"the verb reached git anyway: {calls}"


def test_the_local_verbs_the_lane_actually_uses_still_run(workspace: Path) -> None:
    """Boundary pin: refusing everything would pass the test above and break
    the lane. Every verb SafeVCS itself issues must survive."""
    calls, runner = _spy()
    vcs = SafeVCS(workspace=workspace, runner=runner)
    for verb, args in (
        ("rev-parse", ("--abbrev-ref", "HEAD")),
        ("status", ("--porcelain",)),
        ("add", ("-A",)),
        ("branch", ("--list",)),
        ("checkout", ("-b", "tmp-x")),
        ("reset", ("--hard", "HEAD")),
        ("clean", ("-fd",)),
    ):
        vcs._git(verb, *args)
    assert len(calls) == 7


def test_an_option_before_the_verb_does_not_smuggle_one_past(workspace: Path) -> None:
    """`commit` passes `-c user.name=…` ahead of the verb, so the check cannot
    simply read argv[0] — and that skipping must not become a hiding place."""
    calls, runner = _spy()
    vcs = SafeVCS(workspace=workspace, runner=runner)
    vcs._git("-c", "user.name=x", "-c", "user.email=y", "commit", "-m", "m")
    assert len(calls) == 1
    with pytest.raises(VcsError):
        vcs._git("-c", "user.name=x", "push", "origin", "HEAD")
    assert len(calls) == 1, "a leading -c option smuggled a remote verb through"
