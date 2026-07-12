"""Referent resolution for local critique / show-only turns (plan critique PR1).

The original failure mode was not merely \"low evidence\" — the agent failed to
decide *what* to analyse among: the current user text, a turn-scoped file hint,
session artifacts, a prior turn, or (last) memory. This module is a pure,
deterministic resolver with **no LLM** and **no side effects**.

Important invariants (critique v2)
----------------------------------
* The current user message is **not** world-fact evidence. When it is selected
  as material, it is exposed only as ``analysis_target_excerpt`` (data channel).
* ``user_explicit`` is **not** added here; this module never writes an evidence
  chain and never auto-verifies claims.
* Persistent memory is never a silent primary for local critique.
* No auto ``file_read`` / planner bypass — a trusted file hint yields
  ``needs_tool`` so a later stage can decide how to read it.
* Conflict between multiple high-scoring candidates → ``ambiguous``, never a
  random cached artifact.

Feature flag
------------
Callers gate on :data:`FEATURE_FLAG` (default off). This module alone changes
no loop behaviour until a later PR wires it behind the flag.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

# Shadow / rollout flag — loop wiring (PR2+) must default this to False.
FEATURE_FLAG = "referent_resolver_v1"
FEATURE_FLAG_DEFAULT = False
_ENV_MODE = "AGENT_REFERENT_RESOLVER"


def referent_resolver_mode() -> str:
    """Return ``off`` (default), ``shadow`` (log only), or ``on``.

    ``shadow`` logs decisions only. ``on`` enables the local-critique answer
    path when :func:`is_local_critique_eligible` is true (PR2).
    """
    raw = (os.getenv(_ENV_MODE) or "").strip().lower()
    if raw in ("on", "true", "1", "yes"):
        return "on"
    if raw in ("shadow",):
        return "shadow"
    return "off"


# Kinds that can be critiqued without a tool read (PR2). file_hint / path → PR4.
LOCAL_CRITIQUE_KINDS: frozenset[str] = frozenset(
    {"user_text", "artifact", "prior_turn", "explicit_quote"}
)

ReferentStatus = Literal["resolved", "ambiguous", "unresolved", "needs_tool"]
ReferentKind = Literal[
    "explicit_path",
    "explicit_quote",
    "file_hint",
    "artifact",
    "prior_turn",
    "user_text",
    "memory",
]
TrustLevel = Literal["trusted_path", "session_artifact", "prior_turn", "user_data", "untrusted"]

# How close two top relevance scores must be to count as a conflict.
_AMBIGUITY_MARGIN = 0.08
# Minimum relevance for an artifact / prior-turn to become a candidate.
_MIN_RELEVANCE = 0.18
# Default max age for artifact candidates (seconds).
_DEFAULT_ARTIFACT_TTL_SECONDS = 3600.0

# Points at prior context (not the same-message material).
_ANAPHORA_RE = re.compile(
    r"(?i)\b("
    r"это(?:го|му|м|й)?|эт(?:от|а|у|и|их|ими)|"
    r"того|той|тем|них|него|неё|"
    r"this|that|these|those|it|them|"
    r"выше|предыдущ\w*|prior|previous|above"
    r")\b"
)

_CRITIQUE_RE = re.compile(
    r"(?i)\b("
    r"слаб(?:ые|ых|ости)|критик\w*|разбер\w*|проанализ\w*|"
    r"выяви|покажи|show|critique|review|weak(?:ness(?:es)?)?|"
    r"analyze|analyse|find\s+fault"
    r")\b"
)

_SHOW_ONLY_DIRECTIVE_RE = re.compile(
    r"(?i)\b(только\s+покажи|ничего\s+не\s+делай|only\s+show|do\s+not\s+do\s+anything)\b"
)

_PATH_RE = re.compile(
    r"(?ix)"
    r"("
    r"[A-Za-z]:\\[^\s\"']+"  # Windows path
    r"|/(?:[\w.\-]+/)*[\w.\-]+\.[\w.\-]+"  # Unix-ish file path
    r"|(?:[\w.\-]+/)+[\w.\-]+\.[\w.\-]+"  # relative path with slash
    r"|\b[\w.\-]+\.(?:py|txt|json|yaml|yml|md|log|csv|js|ts|toml)\b"
    r")"
)

_QUOTED_RE = re.compile(
    r"[\"«]([^\"»]{8,800})[\"»]"
    r"|'([^']{8,800})'"
)


def is_critique_directive(text: str) -> bool:
    """True when the user asks to analyse / critique / show weaknesses."""
    return bool(_CRITIQUE_RE.search(text or "") or _SHOW_ONLY_DIRECTIVE_RE.search(text or ""))


def is_show_only_directive(text: str) -> bool:
    """True for show-only directives (no further action offers)."""
    return bool(_SHOW_ONLY_DIRECTIVE_RE.search(text or ""))


def is_local_critique_eligible(decision: ReferentDecision) -> bool:
    """Whether PR2 should take the local-critique / show-only path.

    Requires ``resolved`` primary in :data:`LOCAL_CRITIQUE_KINDS`, a non-empty
    analysis target, and a critique / show-only directive.
    """
    if decision.status != "resolved" or decision.primary is None:
        return False
    if decision.primary.kind not in LOCAL_CRITIQUE_KINDS:
        return False
    if not (decision.analysis_target_excerpt or "").strip():
        return False
    return is_critique_directive(decision.directive_excerpt or "")


def citation_token_for_referent(decision: ReferentDecision) -> str:
    """Citation grammar token for claims about the resolved analysis target."""
    primary = decision.primary
    if primary is None:
        return "[user:target]"
    if primary.kind == "prior_turn":
        tid = primary.turn_id or primary.id
        return f"[prior_turn:{tid}]"
    if primary.kind == "artifact":
        return f"[artifact:{primary.id}]"
    return "[user:target]"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]{3,}", text.casefold())}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass(frozen=True)
class ReferentCandidate:
    """One possible analysis object — never treated as world-fact evidence."""

    kind: ReferentKind
    id: str
    provenance: str
    relevance_score: float
    trust: TrustLevel
    session_id: str | None = None
    turn_id: str | None = None
    freshness_seconds: float | None = None
    label: str = ""
    excerpt: str = ""  # data channel only; untrusted

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "provenance": self.provenance,
            "relevance_score": round(self.relevance_score, 4),
            "trust": self.trust,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "freshness_seconds": self.freshness_seconds,
            "label": self.label,
            "excerpt_chars": len(self.excerpt),
        }


@dataclass(frozen=True)
class ReferentDecision:
    """Outcome of one resolution pass."""

    status: ReferentStatus
    candidates: tuple[ReferentCandidate, ...] = ()
    primary: ReferentCandidate | None = None
    conflict_reason: str = ""
    analysis_target_excerpt: str = ""  # data, not instruction
    directive_excerpt: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidates": [c.to_dict() for c in self.candidates],
            "primary": None if self.primary is None else self.primary.to_dict(),
            "conflict_reason": self.conflict_reason,
            "analysis_target_excerpt_chars": len(self.analysis_target_excerpt),
            "directive_excerpt_chars": len(self.directive_excerpt),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ArtifactRef:
    """Session-scoped artifact metadata (bodies stay out of the resolver)."""

    id: str
    session_id: str
    label: str = ""
    tool: str = ""
    path: str = ""
    turn_id: str | None = None
    created_at: datetime | None = None
    preview: str = ""  # short untrusted preview for relevance only


@dataclass(frozen=True)
class PriorTurnRef:
    turn_id: str
    session_id: str
    question: str = ""
    answer: str = ""
    timestamp: datetime | None = None


@dataclass(frozen=True)
class FileHintRef:
    """Turn-scoped file hint — must match ``current_turn_id`` to be eligible."""

    path: str
    turn_id: str
    session_id: str | None = None


@dataclass(frozen=True)
class MemoryRef:
    """Optional memory hit — never silent primary for local critique."""

    id: str
    excerpt: str = ""
    relevance_score: float = 0.0


@dataclass
class ReferentResolver:
    """Pure heuristic resolver. No I/O, no LLM."""

    workspace_root: Path | None = None
    artifact_ttl_seconds: float = _DEFAULT_ARTIFACT_TTL_SECONDS
    now: Any = field(default=None)  # injectable clock: () -> datetime

    def resolve(
        self,
        question: str,
        *,
        current_session_id: str,
        current_turn_id: str,
        file_hint: FileHintRef | str | None = None,
        artifacts: Sequence[ArtifactRef] = (),
        prior_turns: Sequence[PriorTurnRef] = (),
        memory_hits: Sequence[MemoryRef] = (),
    ) -> ReferentDecision:
        if not isinstance(question, str) or not question.strip():
            return ReferentDecision(status="unresolved", notes=("empty_question",))

        text = question.strip()
        directive = text
        q_tokens = _token_set(text)
        notes: list[str] = []
        candidates: list[ReferentCandidate] = []

        # 1) Explicit path / filename in the question
        for match in _PATH_RE.finditer(text):
            raw = match.group(0).rstrip(".,);]")
            trusted, trust_note = self._trust_path(raw)
            if not trusted:
                notes.append(trust_note or f"path_rejected:{raw}")
                continue
            candidates.append(
                ReferentCandidate(
                    kind="explicit_path",
                    id=f"path:{raw}",
                    provenance="question_path",
                    relevance_score=0.95,
                    trust="trusted_path",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    label=raw,
                    excerpt=raw,
                )
            )

        # Explicit long quote in the question → user_text material (data)
        for match in _QUOTED_RE.finditer(text):
            quoted = next(g for g in match.groups() if g)
            candidates.append(
                ReferentCandidate(
                    kind="explicit_quote",
                    id=f"quote:{hash(quoted) & 0xFFFFFFFF:08x}",
                    provenance="question_quote",
                    relevance_score=0.92,
                    trust="user_data",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    label="quoted_span",
                    excerpt=quoted,
                )
            )

        # 2) Turn-scoped file_hint
        hint = self._normalize_hint(file_hint)
        if hint is not None:
            if hint.turn_id != current_turn_id:
                notes.append("stale_file_hint_other_turn")
            else:
                trusted, trust_note = self._trust_path(hint.path)
                if not trusted:
                    notes.append(trust_note or "file_hint_untrusted")
                else:
                    candidates.append(
                        ReferentCandidate(
                            kind="file_hint",
                            id=f"hint:{hint.path}",
                            provenance="turn_file_hint",
                            relevance_score=0.90,
                            trust="trusted_path",
                            session_id=hint.session_id or current_session_id,
                            turn_id=hint.turn_id,
                            label=hint.path,
                            excerpt=hint.path,
                        )
                    )

        # 3) Session artifacts (fresh + relevant)
        now = self._clock()
        for art in artifacts:
            if art.session_id != current_session_id:
                notes.append(f"cross_session_artifact_excluded:{art.id}")
                continue
            age = None
            if art.created_at is not None:
                age = max(0.0, (now - art.created_at).total_seconds())
                if age > self.artifact_ttl_seconds:
                    notes.append(f"stale_artifact:{art.id}")
                    continue
            blob = " ".join(
                x for x in (art.label, art.tool, art.path, art.preview) if x
            )
            rel = max(
                _jaccard(q_tokens, _token_set(blob)),
                0.25 if art.path and art.path in text else 0.0,
            )
            if rel < _MIN_RELEVANCE and not (
                art.path and Path(art.path).name.lower() in text.casefold()
            ):
                notes.append(f"irrelevant_artifact:{art.id}")
                continue
            if art.path and Path(art.path).name.lower() in text.casefold():
                rel = max(rel, 0.55)
            candidates.append(
                ReferentCandidate(
                    kind="artifact",
                    id=art.id,
                    provenance=f"artifact:{art.tool or 'cache'}",
                    relevance_score=min(0.88, 0.40 + rel),
                    trust="session_artifact",
                    session_id=art.session_id,
                    turn_id=art.turn_id,
                    freshness_seconds=age,
                    label=art.label or art.path or art.id,
                    excerpt=(art.preview or art.path or art.label)[:400],
                )
            )

        # 4) Prior turn on anaphora / critique-of-previous
        anaphora = bool(_ANAPHORA_RE.search(text))
        critique = bool(_CRITIQUE_RE.search(text))
        if prior_turns and (anaphora or critique):
            prev = prior_turns[-1]
            if prev.session_id != current_session_id:
                notes.append("prior_turn_other_session")
            else:
                material = (prev.answer or prev.question or "").strip()
                if material:
                    rel = 0.70 if anaphora else 0.45
                    # Boost if question has almost no self-contained object.
                    if not candidates and critique:
                        rel = max(rel, 0.72)
                    candidates.append(
                        ReferentCandidate(
                            kind="prior_turn",
                            id=prev.turn_id,
                            provenance="prior_turn",
                            relevance_score=rel,
                            trust="prior_turn",
                            session_id=prev.session_id,
                            turn_id=prev.turn_id,
                            label=f"prior_turn:{prev.turn_id}",
                            excerpt=material[:1200],
                        )
                    )

        # Self-contained critique of *this* message: substantial leftover after
        # stripping directives. Show-only phrases are directives, not anaphora.
        if critique and len(text) >= 80:
            material = _CRITIQUE_RE.sub(" ", text)
            material = _SHOW_ONLY_DIRECTIVE_RE.sub(" ", material)
            material = " ".join(material.split())
            # If the only pointer is anaphora and leftover is tiny, prefer prior.
            if len(material) >= 40 and not (anaphora and len(material) < 60):
                candidates.append(
                    ReferentCandidate(
                        kind="user_text",
                        id=f"user:{current_turn_id}",
                        provenance="current_user_text",
                        relevance_score=0.66 if not anaphora else 0.55,
                        trust="user_data",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        label="current_user_text",
                        excerpt=material[:1200],
                    )
                )

        # 5) Memory — never silent primary; recorded only as note/low candidate
        for mem in memory_hits:
            if mem.relevance_score >= 0.5:
                notes.append(f"memory_hit_ignored_as_primary:{mem.id}")

        if not candidates:
            return ReferentDecision(
                status="unresolved",
                directive_excerpt=directive,
                notes=tuple(notes) or ("no_candidates",),
            )

        ranked = tuple(
            sorted(candidates, key=lambda c: (-c.relevance_score, c.kind, c.id))
        )
        top = ranked[0]
        contenders = [
            c
            for c in ranked
            if c.relevance_score >= top.relevance_score - _AMBIGUITY_MARGIN
            and c.kind != top.kind
        ]
        # Same-kind duplicates (e.g. two paths) also ambiguous when close.
        same_kind_close = [
            c
            for c in ranked[1:]
            if c.kind == top.kind
            and c.relevance_score >= top.relevance_score - _AMBIGUITY_MARGIN
        ]
        if contenders or same_kind_close:
            conflict = contenders[0] if contenders else same_kind_close[0]
            return ReferentDecision(
                status="ambiguous",
                candidates=ranked,
                primary=None,
                conflict_reason=(
                    f"{top.kind}:{top.id} vs {conflict.kind}:{conflict.id}"
                ),
                directive_excerpt=directive,
                notes=tuple(notes),
            )

        if top.kind in {"explicit_path", "file_hint"}:
            return ReferentDecision(
                status="needs_tool",
                candidates=ranked,
                primary=top,
                analysis_target_excerpt=top.excerpt,
                directive_excerpt=directive,
                notes=tuple(notes) + ("read_required",),
            )

        return ReferentDecision(
            status="resolved",
            candidates=ranked,
            primary=top,
            analysis_target_excerpt=top.excerpt,
            directive_excerpt=directive,
            notes=tuple(notes),
        )

    def _clock(self) -> datetime:
        if self.now is None:
            return _now()
        return self.now()

    def _normalize_hint(
        self, file_hint: FileHintRef | str | None
    ) -> FileHintRef | None:
        if file_hint is None:
            return None
        if isinstance(file_hint, FileHintRef):
            return file_hint
        path = str(file_hint).strip()
        if not path:
            return None
        # Bare string without turn_id cannot prove turn scope → reject.
        return None

    def _trust_path(self, raw: str) -> tuple[bool, str]:
        """Basic path trust gate (no I/O of file contents).

        Rejects empty, NUL, obvious traversal when a workspace root is set,
        and directory-looking trailing separators. Symlink/size checks belong
        to the later read stage (PR4).
        """
        path_s = (raw or "").strip().strip("\"'")
        if not path_s or "\x00" in path_s:
            return False, "path_empty_or_nul"
        if path_s.endswith(("/", "\\")):
            return False, "path_looks_like_directory"
        try:
            p = Path(path_s)
        except (TypeError, ValueError):
            return False, "path_invalid"
        if self.workspace_root is not None:
            root = self.workspace_root.resolve()
            try:
                # Resolve only when path exists; otherwise join under root.
                candidate = p if p.is_absolute() else (root / p)
                # Do not follow symlinks for the containment check when possible.
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(root)
            except (OSError, ValueError):
                return False, "path_outside_workspace"
            parts = {part.casefold() for part in resolved.parts}
            if ".." in p.parts:
                return False, "path_traversal"
            if parts & {".git", "node_modules"}:
                # still allowed if inside workspace — only block absolute escape
                pass
        if ".." in Path(path_s).parts:
            return False, "path_traversal"
        return True, ""


def artifacts_from_working_memory(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    session_id: str,
) -> list[ArtifactRef]:
    """Adapter: WorkingMemory.artifacts dict → ArtifactRef list (metadata only)."""
    out: list[ArtifactRef] = []
    for key, meta in artifacts.items():
        args = meta.get("arguments") or {}
        path = ""
        if isinstance(args, dict):
            path = str(args.get("path") or args.get("file") or "")
        label = str(meta.get("tool") or "")
        if path:
            label = f"{label}:{path}" if label else path
        preview = str(meta.get("output") or "")[:200]
        turn_index = meta.get("turn_index")
        out.append(
            ArtifactRef(
                id=str(key),
                session_id=session_id,
                label=label,
                tool=str(meta.get("tool") or ""),
                path=path,
                turn_id=None if turn_index is None else f"turn_index:{turn_index}",
                preview=preview,
            )
        )
    return out
