"""Success criterion: the part of it observable without a model, i.e. the paths NAMED in the criterion, read from disk independently of the executor's word; verdicts ``verified`` / ``missing`` / ``unverifiable`` (unknown is not yes)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Path-like token: a base with LATIN letters and an alphabetic extension; Cyrillic is excluded on purpose (a phrase end is not a path).
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.[A-Za-z][A-Za-z0-9]{0,7}")

#: Framing that humans and models put around a file name.
_TRIM = " \t\"'`«»(),;:!?[]{}<>"

#: Extensions more often a sentence end or version than a file. Empty by default: an exception list is where a check silently stops working, so entries come only from measurement.
_IGNORED_SUFFIXES: frozenset[str] = frozenset()

#: Content claims checkable without a model, exactly two unambiguous forms: `status=ready` (pair with an equals sign, no spaces) and `"status": "ready"` (verbatim JSON piece).
#: A colon without quotes is NOT taken on purpose: 'success sign: file ready' is an ordinary phrase.
_KV_PLAIN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,63})=([A-Za-z0-9_.-]{1,64})")
_KV_JSON = re.compile(
    r"[\"']([A-Za-z_][A-Za-z0-9_]{0,63})[\"']\s*:\s*[\"']([^\"']{1,64})[\"']"
)

#: Max bytes of a file read to check a literal; a criterion speaks of a small trace.
_CONTENT_READ_LIMIT = 256 * 1024

#: Third observable form, for CODE edits: `undefined:name@path.py` = 'this module no longer has a top-level definition with that name'; read by AST, no model.
#: A code-edit trace must change only when the edit is done.
_UNDEFINED = re.compile(r"\bundefined:([A-Za-z_][A-Za-z0-9_]{0,99})@([A-Za-z0-9_./\\-]+\.py)\b")


def undefined_claims(success_check: str) -> tuple[tuple[str, str], ...]:
    """(name, path) pairs from `undefined:name@path.py` forms, in order, without repeats."""
    out: list[tuple[str, str]] = []
    for match in _UNDEFINED.finditer(str(success_check or "")):
        pair = (match.group(1), match.group(2).replace("\\", "/"))
        if pair not in out:
            out.append(pair)
    return tuple(out)


def _top_level_names(target: Path) -> set[str] | None:
    import ast

    try:
        tree = ast.parse(target.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def content_claims(success_check: str) -> tuple[tuple[str, str], ...]:
    """(key, value) pairs named by the criterion, without repeats."""
    seen: list[tuple[str, str]] = []
    text = str(success_check or "")
    for pattern in (_KV_JSON, _KV_PLAIN):
        for match in pattern.finditer(text):
            pair = (match.group(1), match.group(2))
            if pair not in seen:
                seen.append(pair)
    return tuple(seen)


def _claim_pattern(key: str, value: str) -> re.Pattern[str]:
    """A pair found in a file in any usual notation (`status=ready` also matches `{"status": "ready"}` and `status: ready`).

    Only separators may stand between key and value; edge boundaries stop `status=ready` matching `ready_to_fail` or `my_status`.
    """
    return re.compile(
        rf"(?<![\w.-])[\"']?{re.escape(key)}[\"']?\s*[:=]\s*"
        rf"[\"']?{re.escape(value)}(?![\w-])[\"']?",
        re.IGNORECASE,
    )


def _claims_met(target: Path, claims: tuple[tuple[str, str], ...]) -> tuple[bool, str]:
    """Whether content claims hold; an unreadable file is 'no' (fail-closed: 'could not read' must not mean 'holds')."""
    if not claims:
        return True, ""
    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(_CONTENT_READ_LIMIT)
    except OSError as exc:
        return False, f"содержимое не прочитано ({type(exc).__name__})"
    unmet = [
        f"{key}={value}" for key, value in claims
        if not _claim_pattern(key, value).search(text)
    ]
    if unmet:
        return False, "не найдено в содержимом: " + ", ".join(unmet)
    return True, ""


def _artifact_tokens(text: str) -> list[tuple[int, str]]:
    """Path-like tokens together with where they are named; the position binds claims to the trace that precedes them."""
    found: list[tuple[int, str]] = []
    for match in _PATH_TOKEN.finditer(str(text or "")):
        token = match.group(0).strip(_TRIM).replace("\\", "/")
        if not token or token.endswith("/"):
            continue
        if token.rsplit(".", 1)[-1].lower() in _IGNORED_SUFFIXES:
            continue
        found.append((match.start(), token))
    return found


def named_artifacts(success_check: str) -> tuple[str, ...]:
    """Paths NAMED in the success criterion, in order, without repeats; a token needs a base and an alphabetic extension (so ``1.5`` does not pass)."""
    seen: list[str] = []
    for _pos, token in _artifact_tokens(success_check):
        if token not in seen:
            seen.append(token)
    return tuple(seen)


def claims_by_artifact(success_check: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Which content claims belong to which trace: a claim belongs to the last trace named BEFORE it; a claim before every trace goes to the first.

    With one trace the behavior is unchanged.
    """
    text = str(success_check or "")
    tokens = _artifact_tokens(text)
    if not tokens:
        return {}
    bound: dict[str, list[tuple[str, str]]] = {}
    for pattern in (_KV_JSON, _KV_PLAIN):
        for match in pattern.finditer(text):
            owner = tokens[0][1]
            for pos, token in tokens:
                if pos < match.start():
                    owner = token
            pair = (match.group(1), match.group(2))
            slot = bound.setdefault(owner, [])
            if pair not in slot:
                slot.append(pair)
    return {name: tuple(pairs) for name, pairs in bound.items()}


def observe_success_check(success_check: str, workspace: Any) -> dict:
    """Read the WORLD and say whether the criterion holds; no model is asked.

    Returns a dict with ``verdict``, ``artifacts``, ``missing``, ``observed``; it also goes into the execution record so 'held' can be rechecked after the run.
    """
    artifacts = named_artifacts(success_check)
    if not artifacts:
        return {
            "verdict": "unverifiable",
            "artifacts": [],
            "claims": [],
            "missing": [],
            "observed": {},
            "reason": "критерий не называет наблюдаемого следа",
        }
    root = Path(workspace or ".")
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    claims = content_claims(success_check)
    owned = claims_by_artifact(success_check)
    observed: dict[str, dict] = {}
    missing: list[str] = []
    reasons: list[str] = []
    for relpath in artifacts:
        target = root / relpath
        # Containment before observation: a criterion must not WIDEN the area the checker looks into; a trace outside the copy does not count for a goal inside it.
        try:
            resolved = target.resolve()
            escaped = root_resolved != resolved and root_resolved not in resolved.parents
        except OSError:
            resolved, escaped = target, True
        if escaped:
            observed[relpath] = {"exists": False, "size": None, "outside": True}
            missing.append(relpath)
            reasons.append(f"{relpath}: ведёт за пределы рабочей копии")
            continue
        try:
            exists = target.exists()
            size = target.stat().st_size if target.is_file() else None
        except OSError:
            exists, size = False, None
        entry: dict[str, Any] = {"exists": bool(exists), "size": size}
        if not exists:
            observed[relpath] = entry
            missing.append(relpath)
            continue
        met, why = _claims_met(target, owned.get(relpath, ()))
        entry["claims_met"] = met
        observed[relpath] = entry
        if not met:
            missing.append(relpath)
            reasons.append(f"{relpath}: {why}")
    for name, relpath in undefined_claims(success_check):
        names = _top_level_names(root / relpath) if relpath not in missing else None
        if names is None or name in names:
            if relpath not in missing:
                missing.append(relpath)
            reasons.append(f"{relpath}: определение {name} всё ещё на месте"
                           if names is not None else f"{relpath}: не читается как модуль")
    return {
        "verdict": "missing" if missing else "verified",
        "artifacts": list(artifacts),
        "claims": [f"{k}={v}" for k, v in claims],
        "missing": missing,
        "observed": observed,
        "reason": (
            ("; ".join(reasons) if reasons else f"не найдено в мире: {', '.join(missing)}")
            if missing
            else f"наблюдалось в мире: {', '.join(artifacts)}"
        ),
    }
