"""Try a patch in a throwaway copy of the repo with ruff and pytest; live code stays untouched."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from core.redaction import redact_text
from core.self_repair_utils import red_before_fix
from tools.base import Risk, Tool, require_ascii_identifier

_BLOCK_RE = re.compile(
    r"^FILE:[ \t]*(?P<path>\S+)[ \t]*\n<<<<<<< SEARCH\n(?P<old>.*?)^=======\n"
    r"(?P<new>.*?)^>>>>>>> REPLACE[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_LINES_RE = re.compile(
    r"^FILE:[ \t]*(?P<path>\S+)[ \t]*\n<<<<<<< LINES[ \t]+(?P<a>\d+)-(?P<b>\d+)[ \t]*\n"
    r"(?P<new>.*?)^>>>>>>> REPLACE[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_TAIL_LINES = 40
_DIFF_LINES = 150
_CONTEXT_LINES = 12
_TIMEOUT_SECONDS = 900


def _lines_new(body: str) -> str:
    """Body of a LINES block: the new text, or "old ======= new", whose new half is taken."""
    parts = re.split(r"^=======\n", body, maxsplit=1, flags=re.MULTILINE)
    return parts[1] if len(parts) == 2 else body


def _matches(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    found = [(m.start(), m.end(), {"path": m["path"], "old": m["old"], "new": m["new"]})
             for m in _BLOCK_RE.finditer(text or "")]
    found += [(m.start(), m.end(), {"path": m["path"], "lines": (int(m["a"]), int(m["b"])),
                                    "new": _lines_new(m["new"])})
              for m in _LINES_RE.finditer(text or "")]
    return sorted(found, key=lambda x: x[0])


def parse_blocks(text: str) -> list[dict[str, Any]]:
    """Patch blocks in text order: SEARCH/REPLACE and replacement by line numbers."""
    return [b for _, _, b in _matches(text)]


#: The valid markers, for the "did you mean" hint.
_MARKERS = ("<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE", "<<<<<<< LINES a-b")
_OPENING = re.compile(r"^<{5,9}\s*(SEARCH|LINES)\b", re.MULTILINE)
_CLOSING = re.compile(r"^>{5,9}\s*REPLACE\b", re.MULTILINE)
_UNIFIED_DIFF = re.compile(r"^(?:--- |\+\+\+ |@@ -\d)", re.MULTILINE)
_VALID_MARKER = re.compile(r"^(?:<{7} SEARCH|={7}|>{7} REPLACE|<{7} LINES \d+-\d+)$")


def after_lines_hint(raw: str, stray: str) -> str:
    """Hint when the text right after a closed LINES block is new text put in the wrong place."""
    at = raw.find(stray.strip()[:80]) if stray.strip() else -1
    before = raw[:at].rstrip() if at > 0 else ""
    if not before.endswith("REPLACE"):
        return ""
    opened = list(_OPENING.finditer(before))
    if not opened or opened[-1].group(1) != "LINES":
        return ""
    return ("новый текст стоит ПОСЛЕ '>>>>>>> REPLACE': в блоке LINES a-b между маркерами пишется "
            "НОВЫЙ текст строк a-b (или «старый / ======= / новый») — перенеси его внутрь блока")


def marker_hint(stray: str) -> str:
    """How stray text differs from a valid block (a "did you mean" hint); "" for prose."""
    import difflib

    lines = stray.splitlines()
    if any(ln.lstrip().startswith("FILE:") and "<<<<" in ln for ln in lines):
        return "строка FILE: — только путь, отдельной строкой; маркер <<<<<<< начинается со следующей строки"
    if _UNIFIED_DIFF.search(stray):
        return ("это формат unified diff (---/+++/@@) — patch_check его не принимает; "
                "перепиши блоком FILE: путь / <<<<<<< LINES a-b / новый текст / >>>>>>> REPLACE")
    if (_OPENING.search(stray) and _CLOSING.search(stray)
            and not any(ln.lstrip().startswith("FILE:") for ln in lines)):
        return "перед блоком нужна строка 'FILE: <путь>' — без неё блок не знает, какой файл править"
    # A malformed marker line first: it is the cause, "not closed" only its consequence.
    for line in lines:
        bare = line.strip()
        if not bare or len(bare) > 40 or _VALID_MARKER.match(bare):
            continue
        up = bare.upper()
        if up == "SEARCH" or up.startswith("SEARCH:"):
            return "маркер пишется целиком: '<<<<<<< SEARCH' — семь знаков '<', пробел, слово"
        if up == "REPLACE":
            return "маркер пишется целиком: '>>>>>>> REPLACE' — семь знаков '>', пробел, слово"
        if up.startswith("LINES "):
            return f"маркер пишется целиком: '<<<<<<< {bare}' — семь знаков '<', пробел, LINES a-b"
        if re.fullmatch(r"<{7} LINES\s*", bare):
            return "в маркере LINES нужны номера строк: '<<<<<<< LINES a-b' — по файлу ДО правки"
        if bare[0] in "<>=":
            close = difflib.get_close_matches(bare, _MARKERS, n=1, cutoff=0.6)
            if close:
                return f"вы имели в виду '{close[0]}'? (у тебя: '{bare}' — маркер ровно семь знаков)"
    if _OPENING.search(stray) and not _CLOSING.search(stray):
        return "блок начат, но не закрыт: последней строкой блока должно стоять '>>>>>>> REPLACE'"
    return ""


def stray_text(text: str) -> list[str]:
    """Text outside any block: it lands nowhere, so it must not be dropped silently."""
    out, pos = [], 0
    for start, end, _ in _matches(text):
        if (text[pos:start]).strip():
            out.append(text[pos:start].strip()[:300])
        pos = end
    if (text or "")[pos:].strip():
        out.append(text[pos:].strip()[:300])
    return out


def _apply_line_blocks(root: Path, blocks: list[dict[str, Any]]) -> list[str]:
    """Line-number replacements, numbered by the file before the patch, applied bottom-up."""
    errors: list[str] = []
    by_file: dict[str, list[dict[str, Any]]] = {}
    for b in blocks:
        by_file.setdefault(b["path"], []).append(b)
    for rel, items in by_file.items():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            errors.append(f"LINES {rel}: файла нет в копии — LINES правит только существующий файл; "
                          "новый файл создаётся блоком с ПУСТЫМ SEARCH: <<<<<<< SEARCH / ======= / "
                          "весь текст файла / >>>>>>> REPLACE")
            continue
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        spans = sorted((b["lines"] for b in items), reverse=True)
        bad = [f"{a}-{z}" for a, z in spans if not 1 <= a <= z <= len(lines)]
        overlap = any(spans[i][0] <= spans[i + 1][1] for i in range(len(spans) - 1))
        if bad or overlap:
            errors.append(f"LINES {rel}: диапазоны {bad or 'пересекаются'} вне 1-{len(lines)} или пересекаются")
            continue
        for b in sorted(items, key=lambda b: b["lines"][0], reverse=True):
            a, z = b["lines"]
            new = b["new"] if not b["new"] or b["new"].endswith("\n") else b["new"] + "\n"
            lines[a - 1:z] = new.splitlines(keepends=True)
        target.write_text("".join(lines), encoding="utf-8")
    return errors


def _diff(root: Path, originals: dict[str, str]) -> str:
    import difflib

    out: list[str] = []
    for rel, before in originals.items():
        target = root / rel
        after = target.read_text(encoding="utf-8") if target.is_file() else ""
        out += difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2)
    return "\n".join(out[:_DIFF_LINES]) + ("\n… (diff truncated)" if len(out) > _DIFF_LINES else "")


def _nearest(text: str, old: str) -> str:
    """Where the block's first line sits in the file, verbatim with line numbers, to copy."""
    first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if first and first in ln]
    if not hits:
        return f"первой строки куска {first!r} в файле нет"
    i = hits[0]
    shown = lines[i:i + _CONTEXT_LINES]
    return "в файле с строки {}:\n{}".format(
        i + 1, "\n".join(f"{i + 1 + k:5d}| {ln}" for k, ln in enumerate(shown)))


def _tail(text: str, n: int = _TAIL_LINES) -> str:
    return redact_text("\n".join((text or "").strip().splitlines()[-n:]))[0]


def apply_blocks(root: Path, blocks: list[dict[str, Any]]) -> list[str]:
    """Apply blocks to the copy and return the errors ([] means everything applied).

    LINES blocks go first, numbered by the file before the patch; then SEARCH/REPLACE.
    """
    errors = _apply_line_blocks(root, [b for b in blocks if "lines" in b])
    for n, b in enumerate(blocks, 1):
        if "lines" in b:
            continue
        target = (root / b["path"]).resolve()
        if root.resolve() not in target.parents:
            errors.append(f"блок {n}: путь {b['path']} выходит за копию")
            continue
        # Whitespace-only SEARCH is empty (a new file); a blank line would match everywhere.
        if not b["old"].strip():
            if target.exists():
                errors.append(f"блок {n}: SEARCH пуст (новый файл), но {b['path']} уже есть — "
                              "существующий файл правят блоком LINES a-b или SEARCH с его куском; "
                              "пустой SEARCH только для файла, которого ещё нет")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(b["new"], encoding="utf-8")
            continue
        if not target.is_file():
            errors.append(f"блок {n}: файла {b['path']} нет")
            continue
        text = target.read_text(encoding="utf-8")
        count = text.count(b["old"])
        if count != 1:
            errors.append(f"блок {n} ({b['path']}): кусок SEARCH найден {count} раз, нужно ровно 1; "
                          f"{_nearest(text, b['old'])}")
            continue
        target.write_text(text.replace(b["old"], b["new"]), encoding="utf-8")
    return errors


def _verdict(result: dict[str, Any]) -> dict[str, str]:
    """Green only for a patch that changes something, carries a test, and passes its checks."""
    if not result.get("diff"):
        return {"verdict": "red", "why": "the patch changes nothing (empty diff)"}
    if result.get("tests_exit_code") is None:
        return {"verdict": "red", "why": "no test in the patch — a change without a test proves nothing"}
    if result.get("tests_exit_code") != 0 or result.get("full_exit_code") not in (0, None):
        return {"verdict": "red", "why": "tests are not green"}
    if result.get("witness_exit_code") == 0:
        return {"verdict": "red", "why": (
            "your new test passes on the OLD code too — it does not witness the change; "
            "make it fail before the fix and pass after it")}
    if "witness_exit_code" in result and not red_before_fix(
            {"timed_out": False, "exit_code": result["witness_exit_code"]}):
        return {"verdict": "red", "why": (
            f"on the OLD code your new tests ended with pytest exit code {result['witness_exit_code']} "
            "(no test collected, usage or internal error) — that shows no defect; "
            "the new test must FAIL before the fix")}
    if result.get("ruff_new_errors"):
        return {"verdict": "red", "why": (
            f"the patch adds {result['ruff_new_errors']} ruff error(s) — see the ruff field")}
    return {"verdict": "green", "why": "the change applies, carries a test, and the tests pass"}


_RUFF_FOUND_RE = re.compile(r"Found (\d+) errors?")


def _ruff_errors(code: int, out: str) -> int:
    if code == 0:
        return 0
    found = _RUFF_FOUND_RE.search(out)
    return int(found.group(1)) if found else 1


def _scratch(workspace: Path) -> tempfile.TemporaryDirectory[str]:
    """Scratch dir beside the workspace, readable by the probe sandbox user (unlike /tmp)."""
    scratch = tempfile.TemporaryDirectory(prefix="patch_check_", dir=workspace.parent)
    os.chmod(scratch.name, 0o755)  # noqa: S103 — a repository copy holds no secrets
    return scratch


#: How many neighbour tests (importing a changed module) join the run.
_NEIGHBOR_LIMIT = 15


def _neighbor_tests(root: Path, source: list[str], exclude: set[str]) -> list[str]:
    """Existing tests that import the changed source modules."""
    import re

    mods = [p[:-3].replace("/", ".") for p in source if p.endswith(".py")]
    if not mods:
        return []
    pattern = re.compile(r"^\s*(?:from|import)\s+(" + "|".join(re.escape(m) for m in mods) + r")\b",
                         re.MULTILINE)
    found: list[str] = []
    for test in sorted((root / "tests").glob("test_*.py")):
        rel = test.relative_to(root).as_posix()
        if rel in exclude:
            continue
        try:
            if pattern.search(test.read_text(encoding="utf-8", errors="replace")):
                found.append(rel)
        except OSError:
            continue
        if len(found) >= _NEIGHBOR_LIMIT:
            break
    return found


def patched_contents(workspace: Path, patch_rel: str) -> dict[str, str]:
    """Full file contents after the patch, for the regular apply path."""
    blocks = parse_blocks((Path(workspace) / patch_rel).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="patch_apply_") as tmp:
        copy = Path(tmp) / "repo"
        PatchCheckTool(workspace_root=Path(workspace))._clone(copy)
        errors = apply_blocks(copy, blocks)
        if errors:
            raise ValueError("; ".join(errors))
        return {p: (copy / p).read_text(encoding="utf-8") for p in dict.fromkeys(b["path"] for b in blocks)}


#: Deciding fields first and the diff last: the agent is shown only the first 6000 characters.
_ORDER = ("verdict", "why", "applied", "where", "errors", "tests_exit_code", "tests_output",
          "full_exit_code", "full_output", "ruff", "ruff_new_errors", "files", "diff")
#: Says where the patch landed: "applied: True" alone was read as "already in the file".
_WHERE = ("a throwaway copy — no file on disk changed; every check starts again from the files "
          "on disk, so the patch file must hold the WHOLE change against them")


def _ordered(result: dict[str, Any]) -> dict[str, Any]:
    out = {k: result[k] for k in _ORDER if k in result}
    out.update({k: v for k, v in result.items() if k not in out})
    if isinstance(out.get("diff"), str) and len(out["diff"]) > 3000:
        out["diff"] = out["diff"][:3000] + "\n… (diff truncated; the verdict and test output above decide)"
    return out


class PatchCheckTool(Tool):
    name = "patch_check"
    description = (
        "Check YOUR OWN code change before anyone applies it: reads a patch file of "
        "blocks: FILE: path / <<<<<<< SEARCH / exact old text / ======= / new text / "
        ">>>>>>> REPLACE (empty SEARCH = new file), OR - easier, no copying of old text - "
        "FILE: path / <<<<<<< LINES 284-292 / new text for those lines / >>>>>>> REPLACE "
        "(or LINES a-b / old text / ======= / new text / >>>>>>> REPLACE) "
        "(line numbers exactly as file_read shows them, before the change). It applies it to a clean copy "
        "of the repository OUTSIDE the workspace, runs ruff and pytest there and returns "
        "the output and the resulting diff. The workspace is never changed. If a SEARCH block is not found the "
        "result shows the real file lines to copy from. Arguments: path (the patch file, "
        "e.g. proposals/selffix/<name>/edits.txt), tests (optional list of test files), "
        "full (true = also run the whole suite)."
    )
    risk: Risk = "reversible"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        return "reversible"

    def _clone(self, dest: Path) -> None:
        subprocess.run(["git", "clone", "-q", str(self.workspace_root), str(dest)],  # noqa: S603, S607 — own paths, no shell
                       check=True, capture_output=True, timeout=300)
        example = self.workspace_root / ".env.example"
        if example.exists():
            shutil.copy(example, dest / ".env.example")

    def _run(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        try:
            # argv from own parts, paths checked; env as in run_tests, without API keys.
            from tools.run_tests import RunTestsTool

            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",  # noqa: S603
                               errors="replace", timeout=_TIMEOUT_SECONDS, check=False,
                               env=RunTestsTool._build_env())
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return -1, f"timeout {_TIMEOUT_SECONDS}s"

    def run(self, path: str, tests: list[str] | None = None, full: bool = False) -> dict[str, Any]:
        require_ascii_identifier(path, role="patch_check path")
        patch = (self.workspace_root / path).resolve()
        if self.workspace_root not in patch.parents or not patch.is_file():
            raise FileNotFoundError(f"patch file not found inside the workspace: {path}")
        raw = patch.read_text(encoding="utf-8")
        blocks = parse_blocks(raw)
        stray = stray_text(raw)
        if stray:
            return {"applied": False, "verdict": "red", "why": "text outside blocks",
                    "errors": [f"text outside any block is ignored — put it inside a block: {t!r}"
                               + (f" | подсказка: {h}" if (h := after_lines_hint(raw, t) or marker_hint(t)) else "")
                               for t in stray]}
        if not blocks:
            return {"applied": False, "verdict": "red", "why": "no blocks",
                    "errors": ["в файле нет блоков FILE:/<<<<<<< SEARCH/=======/>>>>>>> REPLACE или <<<<<<< LINES a-b"]}
        with _scratch(self.workspace_root) as tmp:
            copy = Path(tmp) / "repo"
            self._clone(copy)
            files = list(dict.fromkeys(b["path"] for b in blocks))
            originals = {f: (copy / f).read_text(encoding="utf-8") if (copy / f).is_file() else "" for f in files}
            py = [b["path"] for b in blocks if b["path"].endswith(".py")]
            # Ruff errors already in the files are not the patch's: only the increase counts.
            before = [p for p in dict.fromkeys(py) if (copy / p).is_file()]
            ruff_before = _ruff_errors(*self._run([sys.executable, "-m", "ruff", "check", *before], copy)) \
                if before else 0
            errors = apply_blocks(copy, blocks)
            result: dict[str, Any] = {"applied": not errors, "where": _WHERE, "errors": errors, "files": files}
            if errors:
                return {**result, "verdict": "red", "why": "the patch did not apply"}
            result["diff"] = _diff(copy, originals)
            code, out = self._run([sys.executable, "-m", "ruff", "check", *py], copy)
            result["ruff"] = "not installed" if "No module named ruff" in out else (
                "clean" if code == 0 else _tail(out, 20))
            if result["ruff"] != "not installed":
                result["ruff_new_errors"] = max(0, _ruff_errors(code, out) - ruff_before)
            own_tests = [p for p in py if p.startswith("tests/")]
            source = [p for p in py if not p.startswith("tests/")]
            neighbors = _neighbor_tests(copy, source, exclude=set(own_tests) | set(tests or []))
            if neighbors:
                result["neighbor_tests"] = neighbors
            wanted = list(dict.fromkeys([*(tests or []), *own_tests, *neighbors]))
            for t in wanted:
                require_ascii_identifier(t, role="patch_check test path")
            if wanted:
                code, out = self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *wanted], copy)
                result.update(tests_exit_code=code, tests_output=_tail(out))
            else:
                result["tests_exit_code"] = None
                result["tests_output"] = "no test file in the patch and none named — a change without a test proves nothing"
            if result.get("tests_exit_code") == 0 and own_tests and source:
                # Witness: the new test must fail on the old code.
                witness = Path(tmp) / "witness"
                self._clone(witness)
                errs = apply_blocks(witness, [b for b in blocks if b["path"] in own_tests])
                if not errs:
                    code, out = self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                           *own_tests], witness)
                    result.update(witness_exit_code=code, witness_output=_tail(out, 15))
            if full and result.get("tests_exit_code") not in (0, None):
                # Own tests are red: skip the slow full suite until they pass.
                result["full_output"] = "skipped: your own tests are red — make them green first"
            elif full:
                code, out = self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], copy)
                result.update(full_exit_code=code, full_output=_tail(out, 25))
            result.update(_verdict(result))
            return _ordered(result)
