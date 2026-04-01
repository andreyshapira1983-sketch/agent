import json
import os
import sys
import tempfile
import time
import types
import unittest
from enum import Enum
from unittest.mock import patch

from core.module_builder import (
    BuildStatus,
    DynamicRegistry,
    ModuleBuildResult,
    ModuleBuilder,
    _to_class_name,
)


class _FakeVerdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"


class _FakeSandboxResult:
    SAFE = _FakeVerdict.SAFE
    UNSAFE = _FakeVerdict.UNSAFE


class _RunResult:
    def __init__(self, verdict, error=""):
        self.verdict = verdict
        self.error = error


class _SandboxStub:
    def __init__(self, verdict=_FakeVerdict.SAFE, error=""):
        self.verdict = verdict
        self.error = error

    def run_code(self, code):
        return _RunResult(self.verdict, self.error)

    def simulate_action(self, action):
        return _RunResult(self.verdict, self.error)


class _LLMStub:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def infer(self, prompt, max_tokens=0):
        self.calls.append((prompt, max_tokens))
        if self.responses:
            return self.responses.pop(0)
        return ""


class _BrainStub:
    def __init__(self):
        self.calls = []
        self.raise_on_record = False

    def record_lesson(self, **kwargs):
        if self.raise_on_record:
            raise RuntimeError("record failed")
        self.calls.append(kwargs)


class _CoreStub:
    def __init__(self, llm=None, persistent_brain=None):
        self.llm = llm
        self.persistent_brain = persistent_brain


class _MonitoringStub:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.increment_calls = []
        self.raise_in_info = False

    def info(self, msg, source=None, data=None):
        if self.raise_in_info:
            raise RuntimeError("monitoring info failed")
        self.info_calls.append((msg, source, data))

    def warning(self, msg, source=None, data=None):
        self.warning_calls.append((msg, source, data))

    def increment(self, key):
        self.increment_calls.append(key)


def _install_fake_sandbox_module():
    env_mod = types.ModuleType("environment")
    sandbox_mod = types.ModuleType("environment.sandbox")
    sandbox_mod.SandboxResult = _FakeSandboxResult
    return env_mod, sandbox_mod


class TestModuleBuildResult(unittest.TestCase):
    def test_defaults_and_to_dict(self):
        r = ModuleBuildResult("m")
        self.assertEqual(r.status, BuildStatus.FAILED)
        self.assertFalse(r.ok)
        d = r.to_dict()
        self.assertEqual(d["name"], "m")
        self.assertEqual(d["status"], BuildStatus.FAILED)

    def test_ok_property(self):
        r = ModuleBuildResult("x")
        r.status = BuildStatus.SUCCESS
        self.assertTrue(r.ok)


class TestDynamicRegistry(unittest.TestCase):
    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            reg = DynamicRegistry(os.path.join(td, "r.json"))
            self.assertEqual(reg.get_all("agents"), [])

    def test_load_valid_file_and_merge_known_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"agents": [{"name": "a"}], "x": [1]}, f)
            reg = DynamicRegistry(path)
            self.assertEqual(reg.find("agents", "a")["name"], "a")
            self.assertEqual(reg.get_all("x"), [])

    def test_load_invalid_json_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{")
            reg = DynamicRegistry(path)
            self.assertEqual(reg.get_all("modules"), [])

    def test_save_and_register_find_remove(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.json")
            reg = DynamicRegistry(path)
            reg.register("agents", {"name": "alpha", "v": 1})
            reg.register("agents", {"name": "alpha", "v": 2})
            self.assertEqual(reg.find("agents", "alpha")["v"], 2)
            reg.remove("agents", "alpha")
            self.assertIsNone(reg.find("agents", "alpha"))

    def test_register_unknown_category_created(self):
        with tempfile.TemporaryDirectory() as td:
            reg = DynamicRegistry(os.path.join(td, "r.json"))
            reg.register("custom", {"name": "z"})
            self.assertEqual(reg.find("custom", "z")["name"], "z")

    def test_save_os_error_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            reg = DynamicRegistry(os.path.join(td, "r.json"))
            with patch("core.module_builder.os.replace", side_effect=OSError("x")):
                reg.save()


class TestModuleBuilderPublicApi(unittest.TestCase):
    def test_init_warning_when_sandbox_missing(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=None)
            self.assertIsNone(mb.sandbox)

    def test_build_agent_build_tool_build_module_route_to_build(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            with patch.object(mb, "_build", return_value=ModuleBuildResult("ok")) as pb:
                mb.build_agent("data_analyst", "desc")
                mb.build_tool("pdf_tool", "desc")
                mb.build_module("x_mod", "desc", extra_prompt="extra")
                self.assertEqual(pb.call_count, 3)

    def test_get_summary_counts(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            ok = ModuleBuildResult("a")
            ok.status = BuildStatus.SUCCESS
            bad = ModuleBuildResult("b")
            mb._builds.extend([ok, bad])
            summary = mb.get_summary()
            self.assertEqual(summary["total_builds"], 2)
            self.assertEqual(summary["success"], 1)
            self.assertEqual(summary["failed"], 1)


class TestModuleBuilderBuildFlow(unittest.TestCase):
    def setUp(self):
        self._orig_env = sys.modules.get("environment")
        self._orig_sandbox = sys.modules.get("environment.sandbox")
        env_mod, sandbox_mod = _install_fake_sandbox_module()
        sys.modules["environment"] = env_mod
        sys.modules["environment.sandbox"] = sandbox_mod

    def tearDown(self):
        if self._orig_env is None:
            sys.modules.pop("environment", None)
        else:
            sys.modules["environment"] = self._orig_env
        if self._orig_sandbox is None:
            sys.modules.pop("environment.sandbox", None)
        else:
            sys.modules["environment.sandbox"] = self._orig_sandbox

    def test_build_without_cognitive_core(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=None)
            res = mb._build("m", "M", "p", td, "modules")
            self.assertFalse(res.ok)
            self.assertIn("cognitive_core", res.error)

    def test_build_generate_code_runtime_error(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", side_effect=RuntimeError("down")):
                res = mb._build("m", "M", "p", td, "modules")
                self.assertIn("LLM недоступен", res.error)

    def test_build_generate_code_attribute_error(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", side_effect=AttributeError("no llm")):
                res = mb._build("m", "M", "p", td, "modules")
                self.assertIn("LLM недоступен", res.error)

    def test_build_short_code(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", return_value="x"):
                res = mb._build("m", "M", "p", td, "modules")
                self.assertIn("слишком короткий", res.error)

    def test_build_syntax_error(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            broken = "class M:\n    def run(self):\n        return (\n"
            with patch.object(mb, "_generate_code", return_value=broken):
                res = mb._build("m", "M", "p", td, "modules")
                self.assertIn("SyntaxError", res.error)

    def test_build_missing_class_name(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", return_value="class X:\n    def run(self):\n        return 1\n"):
                res = mb._build("m", "NeedClass", "p", td, "modules")
                self.assertIn("отсутствует", res.error)

    def test_build_rejected_when_no_sandbox(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=None, cognitive_core=object())
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"):
                res = mb._build("m", "A", "p", td, "modules")
                self.assertEqual(res.status, BuildStatus.REJECTED)

    def test_build_sandbox_unsafe(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(
                working_dir=td,
                sandbox=_SandboxStub(verdict=_FakeVerdict.UNSAFE, error="bad"),
                cognitive_core=object(),
            )
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"):
                res = mb._build("m", "A", "p", td, "modules")
                self.assertEqual(res.status, BuildStatus.REJECTED)
                self.assertIn("UNSAFE", res.error)

    def test_build_security_target_outside_workdir(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as out:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"):
                res = mb._build("m", "A", "p", out, "modules")
                self.assertIn("вне working_dir", res.error)

    def test_build_py_compile_fail_with_backup_restore(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            os.makedirs(target, exist_ok=True)
            existing_file = os.path.join(target, "m.py")
            with open(existing_file, "w", encoding="utf-8") as f:
                f.write("class A:\n    pass\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=1, stderr="compile err")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp):
                res = mb._build("m", "A", "p", target, "modules")
                self.assertIn("py_compile провалился", res.error)
                self.assertTrue(os.path.exists(existing_file))

    def test_build_py_compile_fail_without_backup_removes_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=1, stderr="compile err")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp):
                res = mb._build("m", "A", "p", target, "modules")
                self.assertIn("py_compile провалился", res.error)
                self.assertFalse(os.path.exists(os.path.join(target, "m.py")))

    def test_build_smoke_failed_restore(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            os.makedirs(target, exist_ok=True)
            existing_file = os.path.join(target, "m.py")
            with open(existing_file, "w", encoding="utf-8") as f:
                f.write("class A:\n    pass\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=0, stderr="")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp), \
                 patch.object(mb, "_run_core_smoke_if_needed", return_value=(False, "smoke fail")):
                res = mb._build("m", "A", "p", target, "modules")
                self.assertIn("smoke fail", res.error)
                self.assertTrue(os.path.exists(existing_file))

    def test_build_smoke_failed_without_backup_removes_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=0, stderr="")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp), \
                 patch.object(mb, "_run_core_smoke_if_needed", return_value=(False, "smoke fail")):
                res = mb._build("m", "A", "p", target, "modules")
                self.assertIn("smoke fail", res.error)
                self.assertFalse(os.path.exists(os.path.join(target, "m.py")))

    def test_build_import_failed(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=0, stderr="")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp), \
                 patch.object(mb, "_run_core_smoke_if_needed", return_value=(True, None)), \
                 patch.object(mb, "_import_file", return_value=None):
                res = mb._build("m", "A", "p", target, "modules")
                self.assertIn("importlib не смог", res.error)

    def test_build_success_and_registry(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "modules")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            cp = types.SimpleNamespace(returncode=0, stderr="")
            fake_mod = types.ModuleType("x")
            with patch.object(mb, "_generate_code", return_value="class A:\n    def run(self):\n        return 1\n"), \
                 patch("core.module_builder.subprocess.run", return_value=cp), \
                 patch.object(mb, "_run_core_smoke_if_needed", return_value=(True, None)), \
                 patch.object(mb, "_import_file", return_value=fake_mod):
                res = mb._build("m", "A", "prompt-data", target, "modules")
                self.assertTrue(res.ok)
                self.assertEqual(res.class_name, "A")
                self.assertIsNotNone(mb.registry.find("modules", "m"))

    def test_build_unexpected_exception(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=object())
            with patch.object(mb, "_generate_code", side_effect=ValueError("boom")):
                res = mb._build("m", "A", "p", td, "modules")
                self.assertIn("Неожиданная ошибка", res.error)


class TestLoadAllFromRegistry(unittest.TestCase):
    def test_load_registry_branches(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            good = os.path.join(td, "good.py")
            with open(good, "w", encoding="utf-8") as f:
                f.write("class C:\n    pass\n")
            mb.registry.register("modules", {"name": "miss", "class_name": "C", "file_path": os.path.join(td, "nope.py")})
            mb.registry.register("modules", {"name": "badimp", "class_name": "C", "file_path": good})
            mb.registry.register("modules", {"name": "ok", "class_name": "C", "file_path": good})

            calls = {"n": 0}

            def fake_import(path, name):
                calls["n"] += 1
                if name == "badimp":
                    return None
                mod = types.ModuleType(name)
                class C:
                    pass
                mod.C = C
                return mod

            with patch.object(mb, "_import_file", side_effect=fake_import):
                out = mb.load_all_from_registry()
            self.assertEqual(len(out["modules"]), 1)
            self.assertEqual(out["modules"][0]["name"], "ok")


class TestSmokeAndEvents(unittest.TestCase):
    def test_smoke_not_core_file(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            ok, err = mb._run_core_smoke_if_needed(os.path.join(td, "x.py"))
            self.assertTrue(ok)
            self.assertIsNone(err)

    def test_smoke_core_no_runner(self):
        with tempfile.TemporaryDirectory() as td:
            core_dir = os.path.join(td, "core")
            os.makedirs(core_dir, exist_ok=True)
            path = os.path.join(core_dir, "x.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x=1\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            ok, err = mb._run_core_smoke_if_needed(path)
            self.assertTrue(ok)
            self.assertIsNone(err)

    def test_smoke_runner_success_and_fail(self):
        with tempfile.TemporaryDirectory() as td:
            core_dir = os.path.join(td, "core")
            os.makedirs(core_dir, exist_ok=True)
            path = os.path.join(core_dir, "x.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x=1\n")
            smoke = os.path.join(td, "smoke_runner.py")
            with open(smoke, "w", encoding="utf-8") as f:
                f.write("print('ok')\n")

            mon = _MonitoringStub()
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), monitoring=mon)

            success_cp = types.SimpleNamespace(returncode=0, stdout="done", stderr="")
            fail_cp = types.SimpleNamespace(returncode=1, stdout="", stderr="bad")

            with patch("core.module_builder.subprocess.run", return_value=success_cp):
                ok, err = mb._run_core_smoke_if_needed(path)
                self.assertTrue(ok)
                self.assertIsNone(err)

            with patch("core.module_builder.subprocess.run", return_value=fail_cp):
                ok, err = mb._run_core_smoke_if_needed(path)
                self.assertFalse(ok)
                self.assertIn("smoke_runner провалился", err)

    def test_record_event_no_monitoring(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), monitoring=None)
            mb._record_core_smoke_event("core_smoke_passed", "x.py", "out")


class TestPromptsAndHints(unittest.TestCase):
    def test_agent_tool_generic_prompts_contain_expected(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), arch_docs=[])
            a = mb._agent_prompt("n", "NAgent", "desc")
            t = mb._tool_prompt("n", "NTool", "desc")
            g = mb._generic_prompt("n", "N", "desc", "extra")
            self.assertIn("NAgent", a)
            self.assertIn("NTool", t)
            self.assertIn("extra", g)

    def test_existing_file_hint_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            self.assertEqual(mb._existing_file_hint(os.path.join(td, "no.py")), "")

    def test_existing_file_hint_io_error(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            with patch("core.module_builder.os.path.isfile", return_value=True), \
                 patch("core.module_builder.open", side_effect=OSError("x")):
                self.assertEqual(mb._existing_file_hint("x.py"), "")

    def test_existing_file_hint_syntax_error(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "a.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("def broken(:\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            hint = mb._existing_file_hint(fp)
            self.assertIn("SyntaxError", hint)

    def test_existing_file_hint_with_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "a.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("class C:\n    def m(self):\n        return 1\n\ndef f():\n    return 2\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            hint = mb._existing_file_hint(fp)
            self.assertIn("class C", hint)
            self.assertIn("def f", hint)

    def test_existing_file_hint_without_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "a.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("1+1\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub())
            self.assertEqual(mb._existing_file_hint(fp), "")

    def test_arch_hint_empty_and_reading(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), arch_docs=[])
            self.assertEqual(mb._arch_hint(), "")

            doc = os.path.join(td, "arch.txt")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("строка\n")
                f.write("Слой 3: uses interface\n")
                f.write("def x\n")
            mb2 = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), arch_docs=[doc, os.path.join(td, "miss.txt")])
            hint = mb2._arch_hint()
            self.assertIn("КОНТЕКСТ", hint)
            self.assertIn("Слой 3", hint)

    def test_arch_hint_no_keyword_lines(self):
        with tempfile.TemporaryDirectory() as td:
            doc = os.path.join(td, "arch2.txt")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("просто текст\nещё строка\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), arch_docs=[doc])
            self.assertEqual(mb._arch_hint(), "")

    def test_arch_hint_25_line_cutoff(self):
        with tempfile.TemporaryDirectory() as td:
            doc = os.path.join(td, "arch3.txt")
            with open(doc, "w", encoding="utf-8") as f:
                for i in range(40):
                    f.write(f"Слой {i}: interface\n")
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), arch_docs=[doc])
            hint = mb._arch_hint()
            self.assertIn("КОНТЕКСТ", hint)


class TestGenerateAndExtract(unittest.TestCase):
    def test_generate_code_llm_missing(self):
        with tempfile.TemporaryDirectory() as td:
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=None)
            with self.assertRaises(RuntimeError):
                mb._generate_code("p", "n", "C")

    def test_generate_code_empty_first_chunk(self):
        with tempfile.TemporaryDirectory() as td:
            llm = _LLMStub(["text without python"])
            core = _CoreStub(llm=llm)
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=core)
            out = mb._generate_code("p", "n", "C")
            self.assertEqual(out, "")

    def test_generate_code_single_pass(self):
        with tempfile.TemporaryDirectory() as td:
            llm = _LLMStub(["```python\nclass C:\n    pass\n```"])
            core = _CoreStub(llm=llm)
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=core)
            out = mb._generate_code("p", "n", "C")
            self.assertIn("class C", out)
            self.assertEqual(len(llm.calls), 1)

    def test_generate_code_continuation_then_stop(self):
        with tempfile.TemporaryDirectory() as td:
            responses = [
                "```python\nclass C:\n    def x(self):\n",
                "```python\n        return 1\n```",
            ]
            llm = _LLMStub(responses)
            core = _CoreStub(llm=llm)
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=core)
            out = mb._generate_code("p", "n", "C")
            self.assertIn("return 1", out)
            self.assertGreaterEqual(len(llm.calls), 2)

    def test_extract_python_full_block_and_fallbacks(self):
        self.assertEqual(
            ModuleBuilder._extract_python("```python\nclass A:\n    pass\n```"),
            "class A:\n    pass",
        )
        self.assertEqual(ModuleBuilder._extract_python("class A:\n    pass"), "class A:\n    pass")
        self.assertEqual(ModuleBuilder._extract_python("hello"), "")

    def test_extract_python_partial_variants(self):
        self.assertIn("class A", ModuleBuilder._extract_python_partial("```python\nclass A:\n    pass\n```"))
        self.assertIn("class A", ModuleBuilder._extract_python_partial("```python\nclass A:\n    pass"))
        self.assertEqual(ModuleBuilder._extract_python_partial("# comment"), "# comment")
        self.assertEqual(ModuleBuilder._extract_python_partial("random"), "")


class TestImportFinishLogAndUtils(unittest.TestCase):
    def test_import_file_success_and_fail(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "m.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            mod = ModuleBuilder._import_file(fp, "m")
            self.assertIsNotNone(mod)

            self.assertIsNone(ModuleBuilder._import_file(os.path.join(td, "missing.py"), "z"))

    def test_import_file_spec_none(self):
        with patch("core.module_builder.importlib.util.spec_from_file_location", return_value=None):
            self.assertIsNone(ModuleBuilder._import_file("x.py", "m"))

    def test_finish_with_persistent_brain_success_and_failure(self):
        with tempfile.TemporaryDirectory() as td:
            brain = _BrainStub()
            core = _CoreStub(llm=_LLMStub(), persistent_brain=brain)
            mb = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), cognitive_core=core)

            ok = ModuleBuildResult("ok")
            ok.status = BuildStatus.SUCCESS
            mb._finish(ok, time.time() - 0.01)
            self.assertEqual(len(brain.calls), 1)

            brain.raise_on_record = True
            bad = ModuleBuildResult("bad")
            mb._finish(bad, time.time() - 0.01)

    def test_log_paths(self):
        with tempfile.TemporaryDirectory() as td:
            mb_no_mon = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), monitoring=None)
            mb_no_mon._log("x")

            mon = _MonitoringStub()
            mb_mon = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), monitoring=mon)
            mb_mon._log("x")
            self.assertTrue(mon.info_calls)

            mon2 = _MonitoringStub()
            mon2.raise_in_info = True
            mb_mon2 = ModuleBuilder(working_dir=td, sandbox=_SandboxStub(), monitoring=mon2)
            mb_mon2._log("x")

    def test_to_class_name(self):
        self.assertEqual(_to_class_name("data_analyst"), "DataAnalyst")


if __name__ == "__main__":
    unittest.main()
