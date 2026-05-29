"""One-shot REPL harness for live agent inspection.

Usage:
    python scripts/_oneshot.py "your question here"

Prints, in order:
    1. The final answer the user would see (post-Verifier).
    2. The Verifier report (verified vs unverified chunks).
    3. The full ProvenanceChain (what evidence was collected).
    4. The tool_call / tool_result sequence from the JSONL log.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKSPACE / ".env")

from main import build_agent  # noqa: E402


def _load_events(trace_id: str) -> list[dict]:
    path = WORKSPACE / "logs" / f"{trace_id}.jsonl"
    out: list[dict] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/_oneshot.py 'question' [file_hint]")
        return 2
    question = sys.argv[1]
    file_hint = sys.argv[2] if len(sys.argv) >= 3 else None

    print("=" * 70)
    print("QUESTION:")
    print(question)
    print("=" * 70)

    agent = build_agent(workspace=WORKSPACE, with_memory=False)

    answer = agent.run(user_question=question, file_hint=file_hint)

    print()
    print("=" * 70)
    print("FINAL ANSWER (post-Verifier, post-redaction):")
    print("=" * 70)
    print(answer)

    print()
    print("=" * 70)
    print("VERIFIER REPORT:")
    print("=" * 70)
    report = agent.last_verification
    if report is None:
        print("(Verifier was disabled — no report.)")
    else:
        print(f"  total_chunks:               {report.total_chunks}")
        print(f"  verified_chunks:            {report.verified_chunks}")
        print(f"  unverified_chunks:          {report.unverified_chunks}")
        print(f"  cited_but_unmatched_chunks: {report.cited_but_unmatched_chunks}")
        print(f"  fully_unverified:           {report.fully_unverified}")
        print(f"  chain_was_empty:            {report.chain_was_empty}")
        print(f"  disclaimer_set:             {report.disclaimer is not None}")
        print()
        print("  Per-chunk verdicts:")
        for i, ch in enumerate(report.chunks, 1):
            cit = ", ".join(c.raw for c in ch.citations) or "—"
            print(f"    [{i:2}] {ch.verdict:25}  cit={cit}")

    print()
    print("=" * 70)
    print("PROVENANCE CHAIN (what evidence the agent collected):")
    print("=" * 70)
    chain = agent.last_provenance
    if len(chain) == 0:
        print("  (empty — no tool returned a typed source)")
    else:
        for i, ev in enumerate(chain.evidences, 1):
            print(f"  [{i}] kind={ev.kind}  conf={ev.confidence:.2f}")
            print(f"      source_id   = {ev.source_id}")
            print(f"      obtained_via= {ev.obtained_via}")
            print(f"      content_hash= {ev.content_hash[:16]}...")
            print(f"      claim       = {ev.claim}")
            excerpt = ev.excerpt[:160].replace("\n", " ")
            print(f"      excerpt[160]= {excerpt}{'...' if len(ev.excerpt) > 160 else ''}")
            print()

    print("=" * 70)
    print("TOOL TRACE (from JSONL audit log):")
    print("=" * 70)
    trace_id = agent.log.trace_id
    events = _load_events(trace_id)
    print(f"  trace_id: {trace_id}")
    print(f"  total events: {len(events)}")
    for e in events:
        ev = e.get("event", "?")
        if ev == "planner":
            p = e.get("payload", {})
            tools = p.get("tools_chosen") or []
            print(f"  planner: tools_chosen={tools}  attempt={p.get('attempt')}")
        elif ev == "tool_call":
            p = e.get("payload", {})
            tn = p.get("tool_name") or p.get("tool") or "?"
            print(f"  tool_call: {tn}  args={p.get('arguments')}")
        elif ev == "tool_result":
            status = e.get("status", "?")
            lat = e.get("latency_ms", "?")
            print(f"  tool_result: status={status} latency_ms={lat}")
        elif ev == "evidence_collected":
            p = e.get("payload", {})
            print(f"  evidence_collected: count={p.get('count')} kinds={p.get('kinds')}")
        elif ev == "verification":
            p = e.get("payload", {})
            phase = p.get("phase") or "synth"
            print(
                f"  verification[{phase}]: verified={p.get('verified_chunks')} "
                f"unverified={p.get('unverified_chunks')} "
                f"cited_unmatched={p.get('cited_but_unmatched_chunks')} "
                f"fully_unverified={p.get('fully_unverified')}"
            )
        elif ev == "replan":
            p = e.get("payload", {})
            phase = p.get("phase") or "tool"
            triggers = p.get("triggers") or []
            extra = ""
            if phase == "verify":
                urls = p.get("unresolved_urls") or []
                extra = f"  unresolved_urls={urls}"
            print(
                f"  replan[{phase}]: triggers={triggers} "
                f"attempt={p.get('attempt')}->{p.get('next_attempt')}{extra}"
            )
        elif ev == "verify_replan_noop":
            p = e.get("payload", {})
            print(
                f"  verify_replan_noop: iter={p.get('iteration')} "
                f"unresolved_before={p.get('unresolved_count_before')} "
                f"unresolved_after={p.get('unresolved_count_after')}"
            )
        elif ev == "verify_replan_capped":
            p = e.get("payload", {})
            print(
                f"  verify_replan_capped: attempts={p.get('attempts')} "
                f"hard_cap={p.get('hard_cap')} "
                f"unresolved={p.get('unresolved_count')}"
            )
        elif ev == "respond":
            p = e.get("payload", {})
            print(f"  respond: attempts_used={p.get('attempts_used')} sources={p.get('sources')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
