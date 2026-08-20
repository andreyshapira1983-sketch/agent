"""Stage A: propose a grounded coding task plus its FAILING acceptance test,
and drop exactly one ``self_build_task.approve`` item for a human.

Writes no implementation code, applies nothing, invents no target. A human
blesses the yardstick before any implementation exists; every failure is a
hard veto that creates no inbox item. Stage B (core/self_task_builder.py)
runs only after that blessing, against the FROZEN test. Why it is built
this way: docs/CODE_NOTES.md, "The ladder opens to the organs it was built
for".
"""
from __future__ import annotations

import ast
import base64
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.causal_claim_store import distilled_lessons
from core.lesson_provenance import record_lesson_injections
from core.self_apply_lane import FileChange, _normalize_rel, classify_patch_risk
from core.self_build_producer import (
    _DEFAULT_CONFIDENCE_THRESHOLD,
    _MAX_CONTENT_BYTES,
    ProducerReport,
    RoleOutput,
    _default_file_reader,
    _is_self_build_target_allowed,
    _llm_json,
    _looks_like_diff,
)
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# Distinct from ``self_apply_lane.run`` on purpose: approving this blesses a
# spec, it does not start the lane.
SELF_TASK_OPERATION = "self_build_task.approve"

TASK_PRODUCER_ORIGIN = "subagent_self_task_producer"

_CODE_TODO_SOURCE = "code_todo"

#: Signal classes Stage A may take work from. `oversized_module` is excluded BY
#: NAME, not by oversight: its target is `split:<path>` and its work belongs to
#: the self-build producer. Rationale and the operator ruling that opened this
#: set: tests/test_stage_a_takes_self_measured_work.py.
_SELECTABLE_SIGNAL_SOURCES: frozenset[str] = frozenset({
    _CODE_TODO_SOURCE,
    "architecture_audit",
})


def _selectable_signal_sources() -> frozenset[str]:
    """Классы сигналов, из которых Stage A вправе взять кандидата."""
    return _SELECTABLE_SIGNAL_SOURCES


def _is_diagnosis_target_allowed(target: str) -> bool:
    """Stage A acceptance for a VERIFIED-diagnosis target: critical organs open
    (Stage A only adds a test), path hygiene stays closed. Why that is safe:
    docs/CODE_NOTES.md, "The ladder opens to the organs it was built for".
    """
    rel = str(target or "").replace("\\", "/").strip()
    if not rel:
        return False
    canonical = _normalize_rel(rel)
    if canonical is None:
        return False
    ok, _reason, _rejected = classify_patch_risk(
        [FileChange(path=canonical, content="pass\n")]
    )
    return bool(ok)


def _target_gate_for(source_kind: str) -> Callable[[str], bool]:
    """Диагнозу открыты органы ядра (решение оператора 2026-08-15), TODO — нет:
    ветка А кладёт только новый тест в tests/, цель на этом шаге не редактируется.
    """
    if source_kind in ("verified_diagnosis", "architecture_audit"):
        # Self-analysis lives in the organs, so an audit finding gets the
        # diagnosis-grade gate for the identical reason recorded there.
        return _is_diagnosis_target_allowed
    return _is_self_build_target_allowed


def decode_frozen_test(payload: dict[str, Any]) -> str:
    """The exact frozen acceptance test from a Stage-A approval payload.

    Prefers the redaction-inert base64 copy, falls back to the plain field for
    legacy items. Stage B MUST use this: the plain field may be DLP-mangled.
    """
    b64 = payload.get("test_content_b64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 — fall back to the plaintext preview
            pass
    raw = payload.get("test_content")
    return raw if isinstance(raw, str) else ""


def _default_task_selector(workspace: str | Path) -> Callable[[], Any]:
    """Zero-arg selector for the top-ranked selectable backlog candidate.
    Read-only; any load failure yields ``None`` so the producer refuses.
    """

    def _select() -> Any:
        try:
            from core import backlog_selector

            for candidate in backlog_selector.load_backlog(workspace):
                if str(getattr(candidate, "signal_source", "")) in _SELECTABLE_SIGNAL_SOURCES:
                    return candidate
        except Exception:  # noqa: BLE001 — a broken selector must never break producer
            return None
        return None

    return _select


def _field(obj: Any, name: str) -> str:
    return str(getattr(obj, name, "") or "").strip()


def _unresolved_task(inbox: Any) -> Any | None:
    """The unfinished Stage-A item ITSELF (not a yes/no), so the refusal can
    name its blocker. Unfinished means `pending` OR `approved`-but-unexecuted.
    Why the object and not a flag: docs/CODE_NOTES.md, «The wall that would
    not say its name».
    """
    try:
        items = inbox.list()
    except Exception:  # noqa: BLE001
        return None
    for item in items:
        if getattr(item, "operation", "") != SELF_TASK_OPERATION:
            continue
        if getattr(item, "status", "") in ("pending", "approved"):
            return item
    return None


# ── the task builder (LLM) ──────────────────────────────────────────────────


#: How each source's evidence is presented to the model, keyed by `source_kind`.
#: The frame must name the evidence for what it is: feeding a diagnosis to the
#: model disguised as a "TODO comment" is a lie to the model.
_SOURCE_FRAMES: dict[str, tuple[str, str]] = {
    "code_todo": (
        "a real TODO/FIXME comment from a Python file",
        "TODO/FIXME comment",
    ),
    "verified_diagnosis": (
        (
            "a VERIFIED self-diagnosis from the agent's own audit log: every "
            "claim in it was independently confirmed against evidence. The "
            "acceptance test must REPRODUCE the diagnosed defect"
        ),
        "Verified diagnosis",
    ),
    "architecture_audit": (
        (
            "a PRIORITY GAP the agent's own read-only architecture audit found "
            "in itself — nobody typed it; the audit measured the tree and "
            "named this gap with its evidence files. The acceptance test must "
            "REPRODUCE the gap, so it fails today and passes once the gap is "
            "closed"
        ),
        "Architecture-audit gap",
    ),
}


def _signature_block(impl_path: str, *, max_lines: int = 40) -> str:
    """Real signatures of the target module's public callables, read from the
    running code via `inspect`. Any doubt is silence: honest block or none.
    """
    import importlib
    import inspect

    dotted = impl_path.replace("\\", "/").removesuffix(".py").replace("/", ".")
    try:
        module = importlib.import_module(dotted)
    except Exception:  # noqa: BLE001 — сомнение = молчание
        return ""
    lines: list[str] = []
    for name in sorted(vars(module)):
        if name.startswith("_") or len(lines) >= max_lines:
            continue
        obj = getattr(module, name)
        origin = str(getattr(obj, "__module__", "") or "")
        # Свои реэкспорты — часть API поверхности модуля (RepairProposal живёт в
        # self_repair_models и реэкспортирован в self_repair); чужое — шум.
        own = origin == dotted or origin.split(".", 1)[0] in (
            "core", "tools", "cli", "app",
        )
        if not callable(obj) or not own:
            continue
        try:
            lines.append(f"{name}{inspect.signature(obj)}")
        except (ValueError, TypeError):
            continue
    return "\n".join(lines)


def _lesson_prompt_parts(
    lessons: tuple[Any, ...], impl_path: str,
) -> tuple[str, str]:
    """(system addition, user addition) from lesson digests. A lesson changes
    the PLAN mechanically — `include_real_signatures` injects real signatures
    rather than hoping the model reads prose. No lessons ⇒ the old prompt.
    """
    if not lessons:
        return "", ""
    directives = "\n".join(
        f"- {card.directive} [scope: {card.scope}]" for card in lessons
    )
    system_add = (
        "\nLESSONS from your own verified past (each survived hypothesis, "
        "intervention and an independent-case check):\n" + directives
    )
    user_add = ""
    if any(card.machine_action == "include_real_signatures" for card in lessons):
        block = _signature_block(impl_path)
        if block:
            user_add = (
                "\n\nReal signatures from the running code (the source of "
                "truth — call ONLY with these parameters):\n" + block
            )
    return system_add, user_add


def _task_builder_generate(
    llm: Any, *, impl_path: str, quote: str, evidence_ref: str, current_content: str,
    source_kind: str = "code_todo", lessons: tuple[Any, ...] = (),
) -> RoleOutput:
    """Ask the model for a task spec + a failing acceptance test (never code)."""
    frame, quote_label = _SOURCE_FRAMES.get(source_kind, _SOURCE_FRAMES["code_todo"])
    lesson_system, lesson_user = _lesson_prompt_parts(lessons, impl_path)
    system = (
        "You are the Task Author on a self-build team. You are given "
        f"{frame} and the file's current content. "
        "Propose ONE small, concrete coding task that resolves it, and "
        "write a NEW pytest acceptance test that FAILS today and will PASS once "
        "the task is implemented. Do NOT write the implementation itself. The "
        "test must import from the given implementation module and assert real, "
        "specific behaviour (never a trivial `assert True`). "
        "Reply with ONE JSON object only (no markdown fences, no commentary). "
        "Put the test body as an array of lines so newlines stay valid JSON: "
        "{"
        '"task_title": "<short title>", '
        '"task_summary": "<what to implement, 1-3 sentences>", '
        '"impl_path": "<repo-relative .py file to change>", '
        '"test_path": "tests/test_<name>.py", '
        '"test_lines": ["import ...", "def test_...():", "    assert ..."], '
        '"confidence": <0..1>}.'
        + lesson_system
    )
    user = (
        f"Implementation file: {impl_path}\n"
        f"Evidence (file:line): {evidence_ref}\n"
        f"{quote_label}: {quote}\n\n"
        f"Current content of {impl_path}:\n{current_content or '(empty)'}"
        + lesson_user
    )
    parsed = _llm_json(llm, system=system, user=user, max_tokens=4000)
    if not parsed:
        # One recovery attempt: models often wrap JSON or embed raw newlines
        # inside a single "test_content" string. Re-ask for strict JSON lines.
        parsed = _llm_json(
            llm,
            system=system + " IMPORTANT: previous reply was not valid JSON.",
            user=user + "\n\nReturn ONLY the JSON object. Use test_lines (array of strings).",
            max_tokens=4000,
        )
    if not parsed:
        return RoleOutput("task_builder", "failed", "task author returned no parseable JSON")
    test_content = parsed.get("test_content") or ""
    if not isinstance(test_content, str) or not test_content.strip():
        lines = parsed.get("test_lines")
        if isinstance(lines, list) and lines:
            test_content = "\n".join(str(line) for line in lines)
            if test_content and not test_content.endswith("\n"):
                test_content += "\n"
    data = {
        "task_title": str(parsed.get("task_title") or "").strip(),
        "task_summary": str(parsed.get("task_summary") or "").strip(),
        "impl_path": str(parsed.get("impl_path") or "").replace("\\", "/").strip(),
        "test_path": str(parsed.get("test_path") or "").replace("\\", "/").strip(),
        "test_content": test_content if isinstance(test_content, str) else "",
    }
    try:
        data["confidence"] = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    if not data["task_title"] or not isinstance(data["test_content"], str):
        return RoleOutput("task_builder", "failed", "task author reply missing fields", data)
    if not data["test_content"].strip():
        return RoleOutput("task_builder", "failed", "task author reply missing fields", data)
    return RoleOutput(
        "task_builder",
        "built",
        f"proposed task {data['task_title']!r} with test {data['test_path']}",
        data,
    )


# ── the task critic (anti-garbage) ──────────────────────────────────────────


_MEANINGLESS_ASSERTS = ("assert true", "assert 1", "assert 1 == 1", "assert not false")


def _module_import_stem(impl_path: str) -> str:
    """``core/foo.py`` -> ``foo`` — the importable stem tests should reference."""
    rel = impl_path.replace("\\", "/").strip()
    rel = rel.removesuffix(".py")
    return rel.rsplit("/", 1)[-1]


def _test_references_module(test_content: str, impl_path: str) -> bool:
    """True when the test imports/uses the implementation module by name."""
    stem = _module_import_stem(impl_path)
    if not stem:
        return False
    low = test_content
    # Accept ``from core.foo import ...``, ``import core.foo``, or ``core.foo``.
    dotted = impl_path.replace("\\", "/")
    dotted = dotted.removesuffix(".py")
    dotted = dotted.replace("/", ".")
    return dotted in low or re.search(rf"\b{re.escape(stem)}\b", low) is not None


def _has_meaningful_assert(test_content: str) -> bool:
    lines = [ln.strip().lower() for ln in test_content.splitlines()]
    asserts = [ln for ln in lines if ln.startswith(("assert ", "assert("))]
    if not asserts:
        # pytest.raises / self.assert* also count as real assertions.
        low = test_content.lower()
        return "pytest.raises" in low or ("assert" in low and "def test" in low)
    meaningful = [a for a in asserts if not any(a.startswith(m) for m in _MEANINGLESS_ASSERTS)]
    return bool(meaningful)


def _vacuous_assert_reason(tree: ast.AST) -> str | None:
    """A tautological assert — true on any outcome, a ruler with no marks.
    Structural rule: a node compared with itself (X == X, X in X …) is always
    true, and an Or with such an operand voids the whole assert.
    """

    def always_true(node: ast.expr) -> bool:
        if isinstance(node, ast.Constant):
            return bool(node.value)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return any(always_true(v) for v in node.values)
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            same = ast.dump(node.left) == ast.dump(node.comparators[0])
            reflexive = (ast.Eq, ast.LtE, ast.GtE, ast.In)
            return same and isinstance(node.ops[0], reflexive)
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and always_true(node.test):
            return f"tautological assertion: `{ast.unparse(node.test)}` is always true"
    return None


_PHANTOM_SIGNAL = "phantom_signature_kwargs"
_PHANTOM_REASON_MARK = "that the real signature does not accept"
#: The attribute subtype of the same class (2026-08-17): an invented field
#: instead of an invented kwarg. Both count as recurrence for the lesson.
_PHANTOM_ATTR_MARK = "does not exist on"
_PARSE_FAIL_MARK = "test does not parse"


def _record_critic_measurement(
    workspace: str | Path, lessons: tuple[Any, ...], critic: Any,
) -> None:
    """The critic IS the instrument for the phantom-kwargs class: one row per
    delivered lesson, defect_recurred/defect_absent. A generation it never
    examined (parse failure) is not a measurement.
    """
    if not lessons:
        return
    veto = list(critic.data.get("veto_reasons", []))
    if any(_PARSE_FAIL_MARK in reason for reason in veto):
        return
    phantom = next(
        (r for r in veto
         if _PHANTOM_REASON_MARK in r or _PHANTOM_ATTR_MARK in r),
        "")
    from core.causal_claim_store import load_claims
    from core.lesson_provenance import record_lesson_measurement

    signals = {
        extra["key"]: claim.observation.defect_signals
        for claim, extra in load_claims(workspace)
    }
    for card in lessons:
        if _PHANTOM_SIGNAL not in signals.get(getattr(card, "key", ""), ()):
            continue
        record_lesson_measurement(
            workspace, card.key,
            instrument="self_task_producer.task_critic",
            outcome="defect_recurred" if phantom else "defect_absent",
            detail=phantom or "armed generation showed no phantom kwargs",
        )


def _phantom_kwargs_reason(tree: ast.AST) -> str | None:
    """A kwarg the real callable does not have: such a test raises TypeError
    today AND after any implementation, so it has no green state. Checked
    against the real signature via importlib; any doubt is silence.
    """
    import importlib
    import inspect as _inspect

    imported: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                try:
                    mod = importlib.import_module(node.module)
                    imported[alias.asname or alias.name] = getattr(
                        mod, alias.name, None
                    )
                except Exception:  # noqa: BLE001, S112 — сомнение = молчание:
                    continue      # сито вычитает мусор, не блокирует на неуверенности
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        target = imported.get(node.func.id)
        if target is None:
            continue
        try:
            params = _inspect.signature(target).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                return (
                    f"call {node.func.id}(...) passes keyword {kw.arg!r} "
                    f"that the real signature does not accept"
                )
    return None


def _diagnosis_linkage_reason(test_content: str, quote: str) -> str | None:
    """A diagnosis-grounded test must mention at least one CODE token of the
    diagnosis (snake_case, CamelCase, paths). Prose words match by accident
    and are not judges.
    """
    carriers = {
        m.group(0)
        for m in re.finditer(
            r"[A-Za-z][\w]*(?:[_.][A-Za-z][\w]*)+"
            r"|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b",
            quote,
        )
        if len(m.group(0)) > 3
    }
    if not carriers:
        return None  # диагнозу нечем связаться — судить не о чем
    if any(c in test_content for c in carriers):
        return None
    sample = ", ".join(sorted(carriers)[:4])
    return f"test mentions none of the diagnosis carriers ({sample}, ...)"


def _task_critic_review(
    build: dict[str, Any],
    *,
    grounded_target: str,
    reader: Callable[[str], str | None],
    confidence_threshold: float,
    quote: str = "",
    source_kind: str = "code_todo",
) -> RoleOutput:
    """Reject garbage tasks BEFORE a human ever sees them. Any failure vetoes."""
    veto: list[str] = []
    impl_path = str(build.get("impl_path") or "").replace("\\", "/").strip()
    test_path = str(build.get("test_path") or "").replace("\\", "/").strip()
    test_content = build.get("test_content") or ""
    confidence = float(build.get("confidence") or 0.0)

    norm_target = grounded_target.replace("\\", "/").strip()
    if not impl_path:
        veto.append("no implementation path")
    elif impl_path != norm_target:
        veto.append(
            f"impl_path {impl_path!r} does not match grounded target {norm_target!r}"
        )
    elif not _target_gate_for(source_kind)(impl_path):
        veto.append(f"impl_path {impl_path!r} is not a low-risk editable file")

    if not test_path:
        veto.append("no test path")
    else:
        if not test_path.startswith("tests/"):
            veto.append(f"test_path {test_path!r} must live under tests/")
        if not test_path.endswith(".py"):
            veto.append(f"test_path {test_path!r} is not a Python file")
        if reader(test_path) is not None:
            veto.append(f"test_path {test_path!r} already exists (must be a new test)")

    if not isinstance(test_content, str) or not test_content.strip():
        veto.append("empty test content")
    else:
        if _looks_like_diff(test_content):
            veto.append("test content looks like a diff, not a full file")
        if len(test_content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            veto.append("test content is too large")
        tree = None
        try:
            tree = ast.parse(test_content)
        except SyntaxError as exc:
            veto.append(f"test does not parse: {exc.msg}")
        if tree is not None:
            # Three structural checks (AST + real signatures) after a live item
            # slipped the string sieve: CODE_NOTES, «The critic that read strings».
            from core.attribute_sieve import phantom_attribute_reason

            for reason in (
                _vacuous_assert_reason(tree),
                _phantom_kwargs_reason(tree),
                # The attribute subtype, measured 2026-08-17: invented
                # claim.state rode past the kwargs sieve twice.
                phantom_attribute_reason(test_content),
                _diagnosis_linkage_reason(test_content, quote)
                if source_kind == "verified_diagnosis" else None,
            ):
                if reason:
                    veto.append(reason)
        if "def test" not in test_content:
            veto.append("test content defines no test function")
        if "[REDACTED:" in test_content:
            veto.append("test content contains redaction markers")
        if impl_path and not _test_references_module(test_content, impl_path):
            veto.append("test does not reference the implementation module")
        if not _has_meaningful_assert(test_content):
            veto.append("test has no meaningful assertion")

    if not build.get("task_title"):
        veto.append("task has no title")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    decision = "veto" if veto else "pass"
    return RoleOutput(
        "task_critic",
        decision,
        "; ".join(veto) if veto else "all task-critic checks passed",
        {"veto_reasons": veto, "confidence": confidence},
    )


# ── the reporter ────────────────────────────────────────────────────────────


def _task_reporter_publish(
    inbox: Any, build: dict[str, Any], evidence: list[str], evidence_ref: str
) -> RoleOutput:
    """Create exactly one Stage-A approval item (inert until Stage B)."""
    impl_path = build["impl_path"]
    test_path = build["test_path"]
    test_content = build["test_content"]
    # A frozen test is source code that must survive the inbox's DLP redactor
    # byte-for-byte, yet may legitimately contain example PII. Hence an exact
    # base64 copy (redaction-inert) plus a human-readable preview that may be
    # scrubbed. Story: docs/CODE_NOTES.md, "The test the redactor ate".
    test_content_b64 = base64.b64encode(test_content.encode("utf-8")).decode("ascii")
    payload = {
        "task_title": build.get("task_title") or "",
        "task_summary": build.get("task_summary") or "",
        "impl_path": impl_path,
        "test_path": test_path,
        "test_content": test_content,
        "test_content_b64": test_content_b64,
        "evidence": list(evidence),
        "evidence_ref": evidence_ref,
        "confidence": float(build.get("confidence") or 0.0),
        "origin": TASK_PRODUCER_ORIGIN,
    }
    digest = hashlib.sha256(
        (impl_path + "\n" + test_content).encode("utf-8")
    ).hexdigest()[:12]
    dedup_key = f"self_task:{impl_path}:{digest}"
    summary = f"coding task: {build.get('task_title') or impl_path} → {impl_path}"
    item = inbox.add(
        operation=SELF_TASK_OPERATION,
        summary=summary,
        risk="reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=dedup_key,
    )
    return RoleOutput(
        "reporter",
        "published",
        f"created task approval item {item.id}",
        {"approval_id": item.id, "dedup_key": dedup_key},
    )


# ── orchestration ───────────────────────────────────────────────────────────


def produce_coding_task(
    *,
    workspace: str | Path,
    inbox: Any,
    llm: Any,
    vcs: Any = None,
    budget_snapshot: Any = None,
    kill_switch: Any = None,
    file_reader: Callable[[str], str | None] | None = None,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    task_selector: Callable[[], Any] | None = None,
    source_kind: str = "code_todo",
) -> ProducerReport:
    """Stage A: publish at most one grounded coding-task proposal for approval.

    Status is one of: budget_kill_switch / budget_wait / task_wait /
    dirty_tree_wait / no_task / task_veto / proposed.
    """
    gates: list[str] = []

    # ── gate 1: budget kill-switch ──────────────────────────────────────────
    if kill_switch is not None and getattr(kill_switch, "active", False):
        return ProducerReport(
            status="budget_kill_switch",
            reason=str(getattr(kill_switch, "reason", "") or "kill-switch active"),
            checked_gates=["kill_switch"],
            next_human_action="Resolve budget kill-switch before proposing a task.",
        )
    gates.append("kill_switch")

    # ── gate 2: hour budget near-exhaustion ─────────────────────────────────
    if budget_snapshot is not None:
        headroom = hour_budget_headroom(budget_snapshot)
        near, reasons = is_budget_near_exhaustion(headroom)
        if near:
            return ProducerReport(
                status="budget_wait",
                reason="; ".join(reasons) or "budget near exhaustion",
                checked_gates=gates + ["budget"],
                next_human_action="Wait for the budget window to refill.",
            )
    gates.append("budget")

    # ── gate 3: a Stage-A task already waits for a human ─────────────────────
    blocking_task = _unresolved_task(inbox)
    if blocking_task is not None:
        _id, _status = getattr(blocking_task, "id", "?"), getattr(blocking_task, "status", "?")
        return ProducerReport(
            status="task_wait",
            reason=f"self_build_task.approve {_id} is {_status} and not executed",
            checked_gates=gates + ["task"],
            next_human_action=(
                f":self-task-build to execute it, or :approval-deny {_id}"
                if _status == "approved"
                else f":approval-approve {_id} or :approval-deny {_id}"
            ),
        )
    gates.append("task")

    # ── gate 4: dirty working tree ──────────────────────────────────────────
    if vcs is not None and not vcs.is_clean():
        return ProducerReport(
            status="dirty_tree_wait",
            reason="git working tree is not clean",
            checked_gates=gates + ["dirty_tree"],
            next_human_action="Commit or stash local changes, then retry.",
        )
    gates.append("dirty_tree")

    reader = file_reader or _default_file_reader(workspace)
    roles: list[RoleOutput] = []

    # ── selector: one grounded code_todo candidate ──────────────────────────
    selector = task_selector or _default_task_selector(workspace)
    try:
        candidate = selector()
    except Exception:  # noqa: BLE001
        candidate = None
    if candidate is None:
        return ProducerReport(
            status="no_task",
            reason="no grounded code TODO/FIXME candidate available",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No grounded coding-task candidate this run.",
        )
    impl_path = _field(candidate, "target_path").replace("\\", "/")
    quote = _field(candidate, "problem_quote")
    evidence_ref = _field(candidate, "evidence_ref")
    if not _target_gate_for(source_kind)(impl_path):
        return ProducerReport(
            status="no_task",
            reason=f"grounded target {impl_path!r} is not a low-risk editable file",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No grounded coding-task candidate this run.",
        )
    roles.append(
        RoleOutput(
            "selector",
            "selected",
            f"selected {impl_path} from {evidence_ref}",
            {"impl_path": impl_path, "evidence_ref": evidence_ref, "quote": quote},
        )
    )

    current_content = reader(impl_path) or ""
    evidence = [f"{source_kind}: {evidence_ref}", f"quote: {quote}"]

    # ── task builder ────────────────────────────────────────────────────────
    lessons = distilled_lessons(workspace)
    builder = _task_builder_generate(
        llm,
        impl_path=impl_path,
        quote=quote,
        evidence_ref=evidence_ref,
        current_content=current_content,
        source_kind=source_kind,
        lessons=lessons,
    )
    roles.append(builder)
    # The prompt has left: delivery is a fact regardless of the verdicts
    # below, and the receipt is what makes causal use measurable at all.
    record_lesson_injections(
        workspace, lessons, consumer="self_task_producer.task_builder")
    if builder.decision != "built":
        return ProducerReport(
            status="task_veto",
            reason=builder.detail or "task author failed",
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=[builder.detail or "task author failed"],
            next_human_action="Task author failed; no approval created.",
        )

    # ── task critic ─────────────────────────────────────────────────────────
    critic = _task_critic_review(
        builder.data, grounded_target=impl_path, reader=reader,
        confidence_threshold=confidence_threshold,
        quote=quote, source_kind=source_kind,
    )
    roles.append(critic)
    _record_critic_measurement(workspace, lessons, critic)
    if critic.decision == "veto":
        return ProducerReport(
            status="task_veto",
            reason=critic.detail,
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(critic.data.get("veto_reasons", [])),
            next_human_action="Task critic vetoed the candidate; no approval created.",
        )

    # ── reporter ────────────────────────────────────────────────────────────
    reporter = _task_reporter_publish(inbox, builder.data, evidence, evidence_ref)
    roles.append(reporter)
    approval_id = reporter.data["approval_id"]
    record_lesson_injections(
        workspace, lessons, consumer="self_task_producer.task_builder",
        action_ref=f"approval:{approval_id}")
    return ProducerReport(
        status="proposed",
        reason=builder.data.get("task_summary") or builder.data.get("task_title") or "",
        target_path=impl_path,
        approval_id=approval_id,
        checked_gates=gates,
        role_outputs=roles,
        next_human_action=(
            f"Review task {approval_id} (read the test!), approve with "
            f":approval-approve {approval_id}, then build it with "
            f":self-task-build {approval_id}."
        ),
    )
