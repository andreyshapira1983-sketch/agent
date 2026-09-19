"""A JSON API answer keeps its facts in view — the README does not bury them.

Web exam 2026-09-19, third run, N04: https://pypi.org/pypi/ruff/json is 1 MB;
`info.requires_python` sat at 20 KB, behind the README in `info.description`,
and the agent sees about 7 KB of an observation. It went for the field through
the lab over the network and was (rightly) refused. Measured live after the
fix: the same answer renders in 2.9 KB with `requires_python` at 2.4 KB.
"""
from __future__ import annotations

import json

from tools.json_view import MAX_STRING, compact_json_view


def _pypi_like(readme_chars: int = 20_000, releases: int = 400) -> str:
    return json.dumps({
        "info": {"description": "R" * readme_chars, "name": "demo", "requires_python": ">=3.9",
                 "version": "4.2.0"},
        "last_serial": 1,
        "releases": {f"0.{i}.0": [{"filename": f"demo-0.{i}.0.tar.gz", "size": 1}] for i in range(releases)},
    })


def test_the_field_behind_the_readme_is_near_the_top() -> None:
    view = compact_json_view(_pypi_like(), truncated=False)
    assert view is not None and view.find('"requires_python": ">=3.9"') < 2000
    assert f"[+{20_000 - MAX_STRING} chars]" in view, "the clipping is stated, not hidden"
    assert '"[count]": 400' in view, "a huge mapping keeps its keys and says how many"


def test_a_body_cut_at_the_size_limit_keeps_its_complete_members() -> None:
    body = _pypi_like()
    cut = body[: body.index('"releases"') + 500]  # cut inside `releases`
    view = compact_json_view(cut, truncated=True)
    assert view is not None and '">=3.9"' in view
    assert "releases" not in view.split("\n", 1)[1], "a half-parsed member must not be shown"
    assert "only complete top-level members" in view


def test_not_json_is_left_alone() -> None:
    assert compact_json_view("<html>hi</html>", truncated=False) is None
    assert compact_json_view('"just a string"', truncated=False) is None
