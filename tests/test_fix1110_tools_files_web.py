"""find_in_files confinement, diff_file secret refusal, run_tests gate, linear HTML stripping."""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from core.models import Action
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.find_in_files import FindInFilesTool
from tools.run_tests import RunTestsTool
from tools.web_fetch import WebFetchTool


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ── find_in_files stays inside the workspace ─────────────────────────────────

def test_find_in_files_refuses_paths_outside_the_workspace(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("needle-outside\n", encoding="utf-8")
    tool = FindInFilesTool(workspace)

    for path in (str(outside), outside.as_posix(), "../outside.txt", str(tmp_path), ".."):
        with pytest.raises(PermissionError):
            tool.run(query="needle", path=path)


# ── diff_file applies the secret-path refusal ────────────────────────────────

@pytest.mark.parametrize("rel", [".env", ".env.local", "secrets/token.txt", "deploy/id_rsa", "tls/server.pem"])
def test_diff_file_refuses_credential_paths(workspace: Path, rel: str):
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("API_TOKEN=plain-value-not-a-known-key-shape\n", encoding="utf-8")
    tool = DiffFileTool(workspace_root=workspace)

    for path in (rel, f"./{rel}", f"sub/../{rel}", str(target)):
        with pytest.raises(PermissionError, match="credential"):
            tool.run(path=path, proposed_content="")


# ── run_tests: declared risk and the gate agree ──────────────────────────────

def _git(workspace: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
        cwd=workspace, check=True, capture_output=True,
    )


@pytest.fixture()
def repo(workspace: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("data/\n__pycache__/\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "base")
    return workspace


def _decision(tool: RunTestsTool, arguments: dict) -> str:
    registry = ToolRegistry()
    registry.register(tool)
    action = Action(step_id="s", type="tool_call", tool_name="run_tests", parameters=arguments)
    return PolicyGate(registry).check(action).decision


def test_a_freshly_written_test_file_waits_for_a_human(repo: Path):
    (repo / "tests" / "test_new.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")
    tool = RunTestsTool(workspace_root=repo)

    assert _decision(tool, {"paths": ["tests/test_new.py"]}) == "escalate"
    assert _decision(tool, {"paths": ["tests"]}) == "escalate"
    assert _decision(tool, {}) == "escalate"


@pytest.mark.parametrize("rel", ["conftest.py", "helper.py", "pytest.ini", "tests/conftest.py"])
def test_new_code_beside_the_targets_waits_for_a_human(repo: Path, rel: str):
    (repo / rel).write_text("\n", encoding="utf-8")

    assert _decision(RunTestsTool(workspace_root=repo), {"paths": ["tests/test_ok.py"]}) == "escalate"


def test_a_target_in_an_ignored_folder_waits_for_a_human(repo: Path):
    (repo / "data").mkdir()
    (repo / "data" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    tool = RunTestsTool(workspace_root=repo)

    assert _decision(tool, {"paths": ["data/test_x.py"]}) == "escalate"
    assert _decision(tool, {"paths": ["data"]}) == "escalate"
    # Not a target and not importable by committed tests: no reason to ask.
    assert _decision(tool, {"paths": ["tests"]}) == "allow"


def test_without_a_git_baseline_the_class_stays_reversible(workspace: Path):
    tool = RunTestsTool(workspace_root=workspace)

    assert tool.risk_for({"paths": ["tests"]}) == "reversible"


def test_malformed_arguments_never_raise_from_risk_for(repo: Path):
    tool = RunTestsTool(workspace_root=repo)

    for arguments in ({"paths": "tests"}, {"paths": [1, None]}, {"paths": ["../x"]}, None):
        assert tool.risk_for(arguments) in {"reversible", "irreversible"}  # type: ignore[arg-type]


# ── web_fetch: stripping is linear and unchanged on well-formed pages ────────

_OLD_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OLD_TAG_RE = re.compile(r"<[^>]+>")


def _old_blocks_and_tags(text: str) -> str:
    return _OLD_TAG_RE.sub(" ", _OLD_SCRIPT_STYLE_RE.sub(" ", text))


@pytest.mark.parametrize("html", [
    "<html><style>.x{color:red}</style><script>alert('x')</script><p>visible</p></html>",
    "<SCRIPT type='a'>x</script >tail<Style>y</STYLE>end",
    "a<script>one</script>b<script>two</script>c",
    "<svg><svg>inner</svg>after</svg>rest",
    "<script>never closed <style>s</style> kept? <p>para</p>",
    "<noscript><script>x</script></noscript>z",
    "<scripts>not a script</scripts> <script-x>y</script>",
    "<template\n id='t'>\n<b>t</b>\n</template\n>text",
    "1 < 2 and 3 > 2 <> <b>bold</b> a<b",
    "<a title=\"x<y\">link</a> << >> <",
    "plain text, no markup",
    "",
])
def test_stripping_matches_the_old_expressions(html: str):
    from tools.web_fetch import _strip_blocks_and_tags

    assert _strip_blocks_and_tags(html) == _old_blocks_and_tags(html)


def test_stripping_matches_the_old_expressions_on_random_markup():
    import random

    rng = random.Random(1110)  # noqa: S311 — seeded test data
    pieces = ["<script>", "</script>", "<style x='1'>", "</STYLE >", "<svg", "</svg>", "<p>",
              "<", ">", "<>", "text", " ", "\n", "<noscript>", "</noscript>", "<b a='<'>", "</"]
    for _ in range(3000):
        html = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
        from tools.web_fetch import _strip_blocks_and_tags

        assert _strip_blocks_and_tags(html) == _old_blocks_and_tags(html), html


@pytest.mark.parametrize(("unit", "tail"), [
    ("<script>x", ""),
    ("<script x", ""),
    ("<script x", ">"),
    ("<style><script>", ""),
    ("<style><script>", "</svg>"),
    ("<", ""),
    ("<a ", ""),
    ("</script <script>", ""),
])
def test_unclosed_tags_are_stripped_in_linear_time(unit: str, tail: str):
    page = unit * (1024 * 1024 // len(unit)) + tail

    started = time.perf_counter()
    WebFetchTool._strip_html(page)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"{elapsed:.1f}s for 1 MiB of {unit!r}"
