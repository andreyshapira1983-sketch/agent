"""Mixin that generates, parses and de-duplicates self-improvement proposals and drives the self-build producer."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from core.autonomous_runtime_types import (
    AutonomousRuntimeConfig,
    AutonomousTask,
    AutonomousTaskReport,
)
from core.budget_governor import BudgetGovernor
from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.injection_guard import prepare_untrusted_text_for_llm
from core.redaction import prepare_text_for_llm_boundary
from core.safe_vcs import SafeVCS
from core.self_build_memory import recent_self_build_lessons, record_self_build_episode
from core.self_build_producer import produce_self_apply_proposal

logger = logging.getLogger(__name__)

_PROPOSAL_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "by", "with",
    "from", "is", "are", "be", "that", "this", "these", "those", "it", "its",
    "as", "at", "across", "into", "their", "there", "which", "what", "than",
    "then", "also", "we", "our", "i", "you", "your", "they", "them", "not",
    "no", "but", "so", "if", "via", "about", "any", "all", "some", "more",
    "most", "less", "few", "each", "other", "such", "may", "can", "will",
    "would", "should", "could", "have", "has", "had", "been", "being",
    "ensure", "help", "use", "uses", "used",
})
_PROPOSAL_JACCARD_THRESHOLD: float = 0.4



def _proposal_stem(token: str) -> str:
    """Strip a few common English suffixes so 'claim'/'claims' collapse."""
    for suffix in ("ization", "ations", "ation", "ings", "ies", "ied", "ing", "ers", "ers", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            base = token[: -len(suffix)]
            if suffix == "ies":
                base += "y"
            return base
    return token


def _proposal_tokens(text: str) -> frozenset[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return frozenset(
        _proposal_stem(t)
        for t in text.split()
        if len(t) > 2 and t not in _PROPOSAL_STOPWORDS
    )


def _proposal_canonical_signature(kind: str, description: str) -> str:
    """Return a deterministic bucket key like 'tests:claim:registry:source'; coarse so the inbox can be grepped."""
    kind = (kind or "other").strip().lower() or "other"
    toks = sorted(_proposal_tokens(description))
    return f"{kind}:{':'.join(toks[:6])}"


def _proposal_jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class AutonomousRuntimeProposals:
    """Mixed into :class:`AutonomousRuntime`; holds no state of its own."""

    def _task_propose(
        self,
        task: AutonomousTask,
        config: AutonomousRuntimeConfig,
        budget: BudgetGovernor,
    ) -> AutonomousTaskReport:
        """Ask the LLM for 1-3 bounded proposals and write them to the approval inbox; never executes them."""
        cap = max(0, int(budget.limits.max_proposals_per_run))
        if cap == 0:
            return AutonomousTaskReport(task, "skipped", "proposals disabled (cap=0)")

        llm = getattr(self.agent, "llm", None)
        if llm is None:
            return AutonomousTaskReport(task, "skipped", "agent has no llm")

        digest = self._proposal_digest()
        existing_hashes, existing_token_sets = self._existing_proposal_fingerprints()

        system = (
            "You propose next tasks for a bounded autonomous coding agent. "
            "Each proposal must be small, reversible, and reviewable by a human. "
            "Reply with STRICT JSON only, matching this schema: "
            "{\"proposals\":[{\"kind\":\"learn|tests|goal|other\","
            "\"description\":\"<<= 160 chars>\",\"rationale\":\"<<= 280 chars>\","
            "\"est_cost\":\"low|medium|high\"}]}. "
            "Return between 1 and 3 proposals. No prose, no markdown."
        )
        user = (
            "Current state digest:\n" + json.dumps(digest, ensure_ascii=False, indent=2)
            + "\n\nReturn JSON with 1-3 distinct proposals."
        )

        safe_user, inj = prepare_untrusted_text_for_llm(
            user, source_label="autonomous_propose_digest"
        )
        if safe_user is None:
            self._log(
                "injection_blocked",
                {"path": "autonomous_propose", **inj.to_log_payload()},
            )
            return AutonomousTaskReport(
                task,
                "failed",
                "proposal input blocked by injection guard",
                {"injection": inj.to_log_payload()},
            )
        user = safe_user

        user, redact_meta = prepare_text_for_llm_boundary(user)
        if redact_meta:
            self._log(
                "redaction_applied",
                {"path": "autonomous_propose", **redact_meta},
            )

        try:
            raw = llm.complete(system=system, user=user, max_tokens=800, temperature=0.4)
        except Exception as exc:  # noqa: BLE001 — the failure is recorded and logged
            self._log("propose_llm_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return AutonomousTaskReport(task, "failed", "llm error", {"error": str(exc)})

        proposals = self._parse_proposals(raw)
        if proposals is None:
            self._log("propose_parse_failed", {"raw": (raw or "")[:400]})
            return AutonomousTaskReport(task, "failed", "proposals JSON malformed", {"raw_head": (raw or "")[:200]})

        written: list[dict] = []
        skipped_dupes = 0
        skipped_semantic = 0
        for proposal in proposals:
            if len(written) >= cap:
                break
            description = (proposal.get("description") or "").strip()
            rationale = (proposal.get("rationale") or "").strip()
            kind = (proposal.get("kind") or "other").strip().lower()
            est_cost = (proposal.get("est_cost") or "low").strip().lower()
            if not description:
                continue
            if kind not in {"learn", "tests", "goal", "other"}:
                kind = "other"
            if est_cost not in {"low", "medium", "high"}:
                est_cost = "low"
            description = description[:160]
            rationale = rationale[:280]
            phash = hashlib.sha256(description.lower().encode("utf-8")).hexdigest()[:16]
            if phash in existing_hashes:
                skipped_dupes += 1
                continue
            tokens = _proposal_tokens(description)
            best_overlap = 0.0
            for prev_kind, prev_tokens in existing_token_sets:
                if prev_kind != kind:
                    continue
                overlap = _proposal_jaccard(tokens, prev_tokens)
                best_overlap = max(best_overlap, overlap)
            if best_overlap >= _PROPOSAL_JACCARD_THRESHOLD:
                skipped_semantic += 1
                self._log(
                    "proposal_semantic_dupe",
                    {
                        "kind": kind,
                        "jaccard": round(best_overlap, 3),
                        "threshold": _PROPOSAL_JACCARD_THRESHOLD,
                        "description": description,
                    },
                )
                continue
            signature = _proposal_canonical_signature(kind, description)
            existing_hashes.add(phash)
            existing_token_sets.append((kind, tokens))
            item = self.approval_inbox.add(
                operation="proposed_task",
                summary=description,
                risk="reversible",
                reasons=(rationale,) if rationale else (),
                payload={
                    "kind": kind,
                    "est_cost": est_cost,
                    "description": description,
                    "rationale": rationale,
                    "hash": phash,
                    "canonical_signature": signature,
                },
                dedup_key=f"proposed_task:{signature}",
            )
            written.append(
                {
                    "id": item.id,
                    "kind": kind,
                    "description": description,
                    "canonical_signature": signature,
                }
            )

        cluster_summary: dict[str, int] = {}
        for entry in written:
            sig = entry.get("canonical_signature") or ""
            cluster_summary[sig] = cluster_summary.get(sig, 0) + 1
        if cluster_summary:
            self._log(
                "proposal_cluster",
                {"written": len(written), "clusters": cluster_summary},
            )

        summary = (
            f"proposals_written={len(written)} "
            f"dupes={skipped_dupes} semantic_dupes={skipped_semantic}"
        )
        return AutonomousTaskReport(
            task,
            "done",
            summary,
            {
                "written": written,
                "skipped_dupes": skipped_dupes,
                "skipped_semantic_dupes": skipped_semantic,
                "raw_count": len(proposals),
            },
        )

    @staticmethod
    def _parse_proposals(raw: str | None) -> list[dict] | None:
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001 — reason stated above
            # First of two attempts: a reply wrapped in prose is ordinary, so stay silent here.
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                # @staticmethod: no self, so the module logger is used.
                logger.warning(
                    "proposal reply has no JSON object (raw_chars=%d)", len(text)
                )
                return None
            try:
                data = json.loads(text[start : end + 1])
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # Both attempts are spent and a paid reply is dropped: log why.
                logger.warning(
                    "proposal reply did not parse after brace-slice rescue "
                    "(raw_chars=%d, sliced_chars=%d): %s: %s",
                    len(text), end + 1 - start, type(exc).__name__, exc,
                )
                return None
        if not isinstance(data, dict):
            return None
        proposals = data.get("proposals")
        if not isinstance(proposals, list):
            return None
        out: list[dict] = []
        for entry in proposals:
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def _existing_proposal_fingerprints(
        self,
    ) -> tuple[set[str], list[tuple[str, frozenset[str]]]]:
        """Return (sha256 hashes, [(kind, token_set)]) of pending proposals for exact and Jaccard dedup."""
        hashes: set[str] = set()
        token_sets: list[tuple[str, frozenset[str]]] = []
        try:
            snap = self.approval_inbox.snapshot()
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Empty sets would silently turn duplicate detection off for this cycle.
            self._log(
                "proposal_dedup_unavailable",
                {"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return hashes, token_sets
        for item in snap.get("items", []) or []:
            if item.get("operation") != "proposed_task":
                continue
            if item.get("status") != "pending":
                continue
            payload = item.get("payload") or {}
            desc = payload.get("description") or item.get("summary") or ""
            kind = (payload.get("kind") or "other").strip().lower() or "other"
            phash = payload.get("hash")
            if isinstance(phash, str) and phash:
                hashes.add(phash)
            elif desc:
                hashes.add(hashlib.sha256(desc.lower().encode("utf-8")).hexdigest()[:16])
            if desc:
                token_sets.append((kind, _proposal_tokens(desc)))
        return hashes, token_sets

    def _proposal_digest(self) -> dict:
        snap = self.approval_inbox.snapshot()
        digest: dict[str, Any] = {
            "source_registry": self._source_counts(),
            "persistent_memory_records": self._memory_count(),
            "approval_inbox_pending": int(snap.get("pending") or 0),
        }
        store = getattr(self.agent, "persistent_store", None)
        if store is not None:
            try:
                records = store.load()
                lessons = [
                    (getattr(r, "content", None) or getattr(r, "text", None) or str(r))[:240]
                    for r in records
                    if "lesson" in (getattr(r, "tags", ()) or ())
                ]
                digest["recent_lessons"] = lessons[-5:]
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # Distinguish "no lessons" from "store would not open" in the log.
                digest["recent_lessons"] = []
                self._log(
                    "proposal_digest_lessons_failed",
                    {"error_type": type(exc).__name__, "error": str(exc)[:200]},
                )
        return digest

    def _has_pending_self_build_proposal(self) -> bool:
        """Return True when a self-apply proposal already awaits human review."""
        try:
            return any(
                item.operation == "self_apply_lane.run"
                for item in self.approval_inbox.pending()
            )
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # False is permissive: an unreadable inbox must be logged, not silent.
            self._log(
                "pending_self_build_check_failed",
                {"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return False

    def _run_self_build_proposal(self, config: AutonomousRuntimeConfig) -> dict | None:
        """Propose (never apply) one low-risk self-build split; journal the outcome; never raises."""
        # No real approval artifacts in a dry run.
        if config.dry_run:
            self._log("self_build_proposal_skipped", {"reason": "dry_run"})
            return None

        agent = self.agent
        model_router = getattr(agent, "model_router", None)
        llm = None
        if model_router is not None:
            try:
                llm = model_router.for_role("synthesizer")
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # None sends the caller down the no-model branch; log it.
                llm = None
                self._log(
                    "self_build_router_unavailable",
                    {"role": "synthesizer", "error_type": type(exc).__name__},
                )
        if llm is None:
            llm = getattr(agent, "llm", None)
        if llm is None:
            self._log("self_build_proposal_skipped", {"reason": "no llm"})
            return None

        # Do not run the expensive producer while an undecided proposal exists.
        if self._has_pending_self_build_proposal():
            self._log(
                "self_build_proposal_skipped",
                {"reason": "pending proposal exists"},
            )
            return None

        try:
            usage_ledger = getattr(model_router, "usage_ledger", None)
            budget_ledger = getattr(usage_ledger, "budget_ledger", None)
            budget_snapshot = (
                budget_ledger.snapshot() if budget_ledger is not None else None
            )
            kill_state = BudgetKillSwitch(
                path=default_path(self.workspace)
            ).status(budget_snapshot)
            report = produce_self_apply_proposal(
                workspace=self.workspace,
                inbox=self.approval_inbox,
                llm=llm,
                vcs=SafeVCS(workspace=self.workspace),
                budget_snapshot=budget_snapshot,
                kill_switch=kill_state,
                lessons_provider=lambda target: recent_self_build_lessons(
                    agent, target
                ),
            )
            result = report.to_dict()
            self._log(
                "self_build_proposal",
                {
                    "status": result.get("status"),
                    "target_path": result.get("target_path"),
                    "approval_id": result.get("approval_id"),
                    "veto_reasons": result.get("veto_reasons"),
                    "reason": result.get("reason"),
                },
            )
            record_self_build_episode(
                agent, kind="self-build-produce", result=result,
                # Same trace_id as this run's answer episode so a reader can join them.
                trace_id=str(getattr(
                    getattr(agent, "log", None), "trace_id", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — the failure is recorded and logged
            self._log(
                "self_build_proposal_error",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            return None
        else:
            return result
