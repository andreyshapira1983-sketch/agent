"""Tests for core.incremental_splitter -- deterministic no-LLM module splitting."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.incremental_splitter import (
    SplitPlan,
    _bound_names,
    _free_refs,
    _uses_forbidden_scope,
    plan_incremental_split,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    return tmp_path


def _write(workspace: Path, rel: str, content: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── guards ───────────────────────────────────────────────────────────────────


def test_refuses_non_python_target(workspace: Path):
    plan = plan_incremental_split(workspace, "core/notes.md")
    assert plan.status == "no_split"


def test_refuses_missing_target(workspace: Path):
    plan = plan_incremental_split(workspace, "core/ghost.py")
    assert plan.status == "no_split"
    assert "does not exist" in plan.reason


def test_refuses_unparseable_target(workspace: Path):
    _write(workspace, "core/broken.py", "def f(:\n")
    plan = plan_incremental_split(workspace, "core/broken.py")
    assert plan.status == "no_split"
    assert "cannot parse" in plan.reason


def test_no_split_when_everything_depends_on_module_state(workspace: Path):
    src = (
        "STATE = {}\n\n\n"
        "def read():\n    return STATE\n\n\n"
        "def write(k, v):\n    STATE[k] = v\n"
    )
    # read/write depend on STATE; STATE is a lone constant -- nothing movable
    # as a self-contained group without it, and a lone constant is refused.
    _write(workspace, "core/stateful.py", src)
    plan = plan_incremental_split(workspace, "core/stateful.py")
    # Either the whole trio moves together (valid) or nothing does; both are
    # safe. What is FORBIDDEN is a partial move that breaks references.
    if plan.status == "planned":
        assert set(plan.step.moved_names) >= {"STATE", "read", "write"}


# ── function mode ────────────────────────────────────────────────────────────


FUNC_SRC = '''"""Module docstring."""
from __future__ import annotations

import json
import os


LIMIT = 10


def helper_a(x):
    return json.dumps(x)


def helper_b(y):
    return helper_a(y) + os.sep


def uses_limit():
    return LIMIT + 1
'''


def test_function_split_moves_closed_group(workspace: Path):
    _write(workspace, "core/funcs.py", FUNC_SRC)
    plan = plan_incremental_split(workspace, "core/funcs.py")
    assert plan.status == "planned", plan.reason
    step = plan.step
    assert step.mode == "functions"
    assert step.new_module == "core/funcs_helpers.py"
    # the dependency-closed group includes LIMIT because uses_limit needs it
    assert set(step.moved_names) == {"LIMIT", "helper_a", "helper_b", "uses_limit"}


def test_function_split_target_reexports_everything(workspace: Path):
    _write(workspace, "core/funcs.py", FUNC_SRC)
    plan = plan_incremental_split(workspace, "core/funcs.py")
    step = plan.step
    kept = _bound_names(ast.parse(step.target_content))
    for name in step.moved_names:
        assert name in kept, f"{name} lost from target import surface"
    assert "from core.funcs_helpers import" in step.target_content


def test_function_split_new_module_is_self_sufficient(workspace: Path):
    _write(workspace, "core/funcs.py", FUNC_SRC)
    plan = plan_incremental_split(workspace, "core/funcs.py")
    step = plan.step
    tree = ast.parse(step.new_content)
    unbound = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            unbound |= _free_refs(node)
    # every free name must be bound at the new module's top level or a builtin
    top = _bound_names(tree)
    import builtins

    leftover = unbound - top - set(dir(builtins))
    assert not leftover, f"new module misses bindings for {leftover}"


def test_function_split_shrinks_target(workspace: Path):
    _write(workspace, "core/funcs.py", FUNC_SRC)
    plan = plan_incremental_split(workspace, "core/funcs.py")
    step = plan.step
    assert step.target_content.count("\n") < FUNC_SRC.count("\n")
    assert "shrinks" in " ".join(step.notes)


def test_function_split_respects_line_budget(workspace: Path):
    big_fn = "def big():\n" + "\n".join(f"    x{i} = {i}" for i in range(500)) + "\n"
    small_fn = "def small():\n    return 1\n"
    _write(workspace, "core/mix.py", big_fn + "\n\n" + small_fn)
    plan = plan_incremental_split(workspace, "core/mix.py", max_move_lines=50)
    if plan.status == "planned":
        assert plan.step.lines_moved <= 50
        assert "big" not in plan.step.moved_names


def test_function_split_skips_globals_users(workspace: Path):
    src = (
        "COUNTER = 0\n\n\n"
        "def bump():\n    global COUNTER\n    COUNTER += 1\n\n\n"
        "def pure(x):\n"
        "    a = x + 1\n"
        "    b = a * 2\n"
        "    c = b - 3\n"
        "    d = c // 4\n"
        "    return a + b + c + d\n"
    )
    _write(workspace, "core/glob.py", src)
    plan = plan_incremental_split(workspace, "core/glob.py")
    assert plan.status == "planned", plan.reason
    assert "bump" not in plan.step.moved_names
    assert "COUNTER" not in plan.step.moved_names
    assert "pure" in plan.step.moved_names


# ── mixin mode ───────────────────────────────────────────────────────────────


def _make_dominant_class_src() -> str:
    methods = []
    for i in range(8):
        methods.append(
            f"    def method_{i}(self, x):\n"
            f"        data = json.dumps(x)\n"
            f"        return self.prefix + data + str({i})\n"
        )
    return (
        '"""Big class module."""\n'
        "from __future__ import annotations\n\n"
        "import json\n\n\n"
        "MODULE_FLAG = True\n\n\n"
        "class Engine:\n"
        "    def __init__(self):\n"
        "        self.prefix = 'p'\n\n"
        "    def uses_flag(self):\n"
        "        return MODULE_FLAG\n\n"
        + "\n".join(methods)
    )


def test_mixin_split_on_dominant_class(workspace: Path):
    _write(workspace, "core/engine.py", _make_dominant_class_src())
    plan = plan_incremental_split(workspace, "core/engine.py")
    assert plan.status == "planned", plan.reason
    step = plan.step
    assert step.mode == "mixin"
    assert step.new_module == "core/engine_methods.py"
    assert "EngineExtractedMethods" in step.new_content
    # __init__ and module-global-touching methods stay behind
    assert "__init__" not in step.moved_names
    assert "uses_flag" not in step.moved_names
    assert all(n.startswith("method_") for n in step.moved_names)


def test_mixin_split_adds_base_and_import(workspace: Path):
    _write(workspace, "core/engine.py", _make_dominant_class_src())
    plan = plan_incremental_split(workspace, "core/engine.py")
    step = plan.step
    assert "class Engine(EngineExtractedMethods):" in step.target_content
    assert "from core.engine_methods import EngineExtractedMethods" in (
        step.target_content
    )
    ast.parse(step.target_content)
    ast.parse(step.new_content)


def test_repeated_mixin_split_uses_unique_base_class(workspace: Path):
    _write(workspace, "core/engine.py", _make_dominant_class_src())
    first_plan = plan_incremental_split(
        workspace, "core/engine.py", max_move_lines=12
    )
    assert first_plan.status == "planned", first_plan.reason
    first = first_plan.step
    assert "class EngineExtractedMethods:" in first.new_content
    _write(workspace, first.target, first.target_content)
    _write(workspace, first.new_module, first.new_content)

    second_plan = plan_incremental_split(
        workspace, "core/engine.py", max_move_lines=12
    )
    assert second_plan.status == "planned", second_plan.reason
    second = second_plan.step
    assert second.new_module == "core/engine_methods2.py"
    assert "class EngineExtractedMethods2:" in second.new_content
    assert "class Engine(EngineExtractedMethods2, EngineExtractedMethods):" in (
        second.target_content
    )

    engine_class = next(
        node
        for node in ast.parse(second.target_content).body
        if isinstance(node, ast.ClassDef) and node.name == "Engine"
    )
    bases = [ast.unparse(base) for base in engine_class.bases]
    assert len(bases) == len(set(bases))

    import sys
    import types

    pkg = types.ModuleType("splitcheck_core")
    pkg.__path__ = []
    sys.modules["splitcheck_core"] = pkg
    try:
        for name, content in (
            ("splitcheck_core.engine_methods", first.new_content),
            ("splitcheck_core.engine_methods2", second.new_content),
        ):
            module = types.ModuleType(name)
            exec(compile(content, f"<{name}.py>", "exec"), module.__dict__)
            sys.modules[name] = module
        target_src = second.target_content.replace("from core.", "from splitcheck_core.")
        target_module = types.ModuleType("splitcheck_core.engine")
        exec(compile(target_src, "<engine.py>", "exec"), target_module.__dict__)
    finally:
        sys.modules.pop("splitcheck_core", None)
        sys.modules.pop("splitcheck_core.engine_methods", None)
        sys.modules.pop("splitcheck_core.engine_methods2", None)


def test_mixin_split_executes_correctly(workspace: Path, tmp_path: Path):
    _write(workspace, "core/engine.py", _make_dominant_class_src())
    plan = plan_incremental_split(workspace, "core/engine.py")
    step = plan.step
    # execute both post-images in one namespace graph to prove behaviour
    import sys
    import types

    pkg = types.ModuleType("splitcheck_core")
    pkg.__path__ = []  # mark as package
    sys.modules["splitcheck_core"] = pkg
    mixin_mod = types.ModuleType("splitcheck_core.engine_methods")
    exec(
        compile(step.new_content, "<engine_methods.py>", "exec"),
        mixin_mod.__dict__,
    )
    sys.modules["splitcheck_core.engine_methods"] = mixin_mod
    target_src = step.target_content.replace(
        "from core.engine_methods import", "from splitcheck_core.engine_methods import"
    )
    target_mod = types.ModuleType("splitcheck_core.engine")
    try:
        exec(compile(target_src, "<engine.py>", "exec"), target_mod.__dict__)
        engine = target_mod.Engine()
        assert engine.method_3(1) == 'p1' + str(3)
        assert engine.uses_flag() is True
    finally:
        sys.modules.pop("splitcheck_core", None)
        sys.modules.pop("splitcheck_core.engine_methods", None)


# ── scope guard helper ───────────────────────────────────────────────────────


def test_forbidden_scope_detection():
    assert _uses_forbidden_scope(ast.parse("def f():\n    global X\n"))
    assert _uses_forbidden_scope(ast.parse("def f(self):\n    return super().f()\n"))
    assert _uses_forbidden_scope(
        ast.parse("def f(self):\n    return self.__hidden\n")
    )
    assert not _uses_forbidden_scope(
        ast.parse("def f(self):\n    return self.__dict__\n")
    )
    assert not _uses_forbidden_scope(ast.parse("def f(x):\n    return x + 1\n"))


# ── live repo sanity ─────────────────────────────────────────────────────────


def test_plans_a_step_for_real_loop_py():
    repo = Path(__file__).resolve().parent.parent
    if not (repo / "core" / "loop.py").is_file():
        pytest.skip("core/loop.py not present")
    plan = plan_incremental_split(repo, "core/loop.py")
    assert plan.status == "planned", plan.reason
    step = plan.step
    # the previously impossible target now yields a provably safe step
    assert step.lines_moved > 0
    ast.parse(step.target_content)
    ast.parse(step.new_content)
    kept = _bound_names(ast.parse(step.target_content))
    if step.mode == "functions":
        assert all(n in kept for n in step.moved_names)


# ── CLI command ──────────────────────────────────────────────────────────────


class _StubLog:
    def __init__(self):
        self.events = []

    def log(self, kind, data):
        self.events.append((kind, data))


class _StubInbox:
    def __init__(self):
        self.items = []

    def add(self, **kwargs):
        class _Item:
            id = "ain_stub123"

        self.items.append(kwargs)
        return _Item()


class _StubAgent:
    def __init__(self):
        self.log = _StubLog()
        self.approval_inbox = _StubInbox()


def test_cli_self_split_publishes_approval_item(workspace: Path, capsys):
    from cli.commands_self_split import _handle_self_split

    _write(workspace, "core/funcs.py", FUNC_SRC)
    agent = _StubAgent()
    assert _handle_self_split("core/funcs.py", agent, workspace) is True
    assert len(agent.approval_inbox.items) == 1
    item = agent.approval_inbox.items[0]
    assert item["operation"] == "self_apply_lane.run"
    paths = [f["path"] for f in item["payload"]["files"]]
    assert "core/funcs.py" in paths
    assert "core/funcs_helpers.py" in paths
    assert item["payload"]["origin"] == "incremental_splitter"
    err = capsys.readouterr().err
    assert "status: proposed" in err
    assert "ain_stub123" in err


def test_cli_self_split_reports_no_split(workspace: Path, capsys):
    from cli.commands_self_split import _handle_self_split

    agent = _StubAgent()
    assert _handle_self_split("core/ghost.py", agent, workspace) is True
    assert agent.approval_inbox.items == []
    err = capsys.readouterr().err
    assert "status: no_split" in err


def test_cli_self_split_usage_on_bad_args(workspace: Path, capsys):
    from cli.commands_self_split import _handle_self_split

    agent = _StubAgent()
    assert _handle_self_split("", agent, workspace) is True
    assert _handle_self_split("a b", agent, workspace) is True
    assert agent.approval_inbox.items == []
    assert "Usage: :self-split" in capsys.readouterr().err


def test_cli_self_split_caps_targeted_test_paths(workspace: Path, monkeypatch):
    """A heavily-imported target must fall back to tests/ instead of blowing
    past the test runner MAX_PATHS argv cap (live regression: core/loop.py)."""
    from cli.commands_self_split import _handle_self_split
    from tools.run_tests import MAX_PATHS
    import core.dependency_map as dm

    _write(workspace, "core/funcs.py", FUNC_SRC)
    many = [f"tests/test_imp_{i}.py" for i in range(MAX_PATHS + 5)]

    class _FakeDep:
        related_tests = many

    monkeypatch.setattr(dm, "build_dependency_map", lambda ws, t: _FakeDep())
    agent = _StubAgent()
    assert _handle_self_split("core/funcs.py", agent, workspace) is True
    item = agent.approval_inbox.items[0]
    assert item["payload"]["test_paths"] == ["tests"]
