"""Live audit harness for the autonomous agent.

Runs a hard-coded battery of scenarios and reports — for each:

  * elapsed wall-time
  * planner LLM calls + total token spend
  * tool calls (tool name + redacted argument summary)
  * unique web hosts touched
  * duplicate (tool, args) detections — anything called twice with
    the same arguments is a cycling suspect
  * unique URLs fetched via web_fetch + any local-network attempts
  * replan events (which phase, which triggers)
  * verifier verdicts (verified / unverified / cited_but_unmatched)
  * attempts_used + cap (`max_total_replans`)
  * a PASS/WARN/FAIL line per check the audit is gated on

Designed to be safe to run head-to-head with the real LLM and the
real internet — every scenario has a per-scenario budget and the
script aborts a single scenario without taking the whole batch down.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKSPACE / ".env")

from main import build_agent  # noqa: E402


# ---------------------------------------------------------------------------
# Scenarios — pinned vocabulary so re-runs are comparable. Each entry
# documents WHAT the audit expects to see; the analyser turns these
# expectations into PASS/WARN/FAIL lines.
# ---------------------------------------------------------------------------

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "introspective",
        "question": (
            "Что ты сейчас понимаешь про самого себя как агента и какую "
            "следующую улучшающую работу видишь?"
        ),
        "expect": {
            "max_attempts": 2,
            # README.md is ~24 KB. Planner sees it (~6 k tokens) and
            # synthesizer sees it (~6 k tokens) -> 12 k input + a
            # multi-paragraph answer (~2 k output) is normal. 50 k
            # leaves headroom for the planner system prompt etc.
            "max_total_tokens": 50_000,
            "should_read_readme": True,
            "should_touch_internet": False,
        },
    },
    {
        "name": "web_search_then_fetch",
        "question": (
            "Найди в интернете актуальное определение понятия 'AI agent' и "
            "приведи кратко с указанием URL источника."
        ),
        "expect": {
            "max_attempts": 3,
            "max_total_tokens": 45_000,
            "should_touch_internet": True,
            "min_web_fetches": 1,
        },
    },
    {
        "name": "direct_url",
        "question": (
            "Открой https://en.wikipedia.org/wiki/Intelligent_agent и "
            "перескажи в трёх фразах что там написано."
        ),
        "expect": {
            "max_attempts": 2,
            "max_total_tokens": 30_000,
            "should_touch_internet": True,
            "expected_hosts": ["en.wikipedia.org"],
            "min_web_fetches": 1,
        },
    },
    {
        "name": "impossible_realtime",
        "question": (
            "Какая прямо сейчас цена биткоина в USD? Если не знаешь, "
            "честно скажи это."
        ),
        "expect": {
            # No hard bound on tools here — we're checking the agent
            # exits gracefully, not how clever it is about real-time
            # data it cannot truly verify.
            "max_attempts": 3,
            "max_total_tokens": 40_000,
            "must_not_loop": True,
        },
    },
    {
        "name": "ambiguous_short",
        "question": "Расскажи про Python.",
        "expect": {
            "max_attempts": 2,
            "max_total_tokens": 30_000,
            "must_not_loop": True,
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LOCAL_HOST_HINTS = (
    "localhost", "127.", "0.0.0.0", "10.", "192.168.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
    "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.",
    "172.31.", "[::1]", "::1",
)


def _is_local_host(host: str) -> bool:
    h = (host or "").lower().strip()
    return any(h.startswith(p) for p in LOCAL_HOST_HINTS)


def _load_events(trace_id: str) -> list[dict[str, Any]]:
    path = WORKSPACE / "logs" / f"{trace_id}.jsonl"
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _canonical_args(args: Any) -> str:
    """Stable key for duplicate detection. Strings end up identical
    across attempts when args are deeply equal."""
    try:
        return json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return repr(args)


# ---------------------------------------------------------------------------
# Per-run analyser
# ---------------------------------------------------------------------------

def _analyse(
    *,
    scenario: dict[str, Any],
    events: list[dict[str, Any]],
    llm_summary: dict[str, Any],
    elapsed_s: float,
    answer: str,
    verification: Any,
    crashed: bool,
) -> dict[str, Any]:
    expect = scenario["expect"]

    # --- raw counts -------------------------------------------------------
    planner_events = [e for e in events if e.get("event") == "planner"]
    tool_calls = [e for e in events if e.get("event") == "tool_call"]
    replan_events = [e for e in events if e.get("event") == "replan"]
    replan_noop = [e for e in events if e.get("event") == "verify_replan_noop"]
    replan_capped = [
        e for e in events if e.get("event") == "verify_replan_capped"
    ]
    verifications = [e for e in events if e.get("event") == "verification"]
    respond = next(
        (e for e in events if e.get("event") == "respond"), None
    )
    attempts_used = (respond or {}).get("payload", {}).get("attempts_used", 0)

    # --- tool call breakdown ----------------------------------------------
    tools_used: list[tuple[str, str]] = []
    web_fetch_urls: list[str] = []
    local_target_attempts: list[str] = []
    for tc in tool_calls:
        p = tc.get("payload", {})
        name = p.get("tool_name") or p.get("tool") or "?"
        args = p.get("arguments") or {}
        tools_used.append((name, _canonical_args(args)))
        if name == "web_fetch":
            url = (args or {}).get("url") or ""
            if isinstance(url, str) and url:
                web_fetch_urls.append(url)
                host = urlsplit(url).hostname or ""
                if _is_local_host(host):
                    local_target_attempts.append(url)

    # --- duplicate detection ---------------------------------------------
    tool_call_counter = Counter(tools_used)
    duplicates = {
        k: c for k, c in tool_call_counter.items() if c > 1
    }

    # --- web host inventory -----------------------------------------------
    hosts: list[str] = []
    for url in web_fetch_urls:
        h = urlsplit(url).hostname or ""
        if h:
            hosts.append(h.lower())
    host_counts = Counter(hosts)

    # --- verifier summary -------------------------------------------------
    if verification is not None:
        ver = {
            "total_chunks": verification.total_chunks,
            "verified_chunks": verification.verified_chunks,
            "unverified_chunks": verification.unverified_chunks,
            "cited_but_unmatched_chunks":
                verification.cited_but_unmatched_chunks,
            "self_declared_chunks": verification.self_declared_chunks,
            "structural_chunks": verification.structural_chunks,
            "disclaimer_set": verification.disclaimer is not None,
        }
    else:
        ver = None

    # --- checks (PASS / WARN / FAIL) --------------------------------------
    checks: list[tuple[str, str, str]] = []  # (level, name, detail)

    def _check(level: str, name: str, detail: str) -> None:
        checks.append((level, name, detail))

    if crashed:
        _check("FAIL", "no_crash", "run() raised an exception")
    else:
        _check("PASS", "no_crash", "run() returned cleanly")

    max_attempts = expect.get("max_attempts")
    if max_attempts is not None:
        if attempts_used > max_attempts:
            _check(
                "FAIL", "attempts_within_budget",
                f"attempts_used={attempts_used} > expected {max_attempts}",
            )
        else:
            _check(
                "PASS", "attempts_within_budget",
                f"attempts_used={attempts_used} <= {max_attempts}",
            )

    max_tokens = expect.get("max_total_tokens")
    if max_tokens is not None:
        total = llm_summary.get("total_tokens", 0)
        if total > max_tokens:
            _check(
                "WARN", "tokens_within_budget",
                f"total_tokens={total} > expected {max_tokens}",
            )
        else:
            _check(
                "PASS", "tokens_within_budget",
                f"total_tokens={total} <= {max_tokens}",
            )

    if expect.get("should_read_readme"):
        read_readme = any(
            t == "file_read" and "README.md" in args
            for t, args in tools_used
        )
        if read_readme:
            _check("PASS", "read_readme",
                   "file_read README.md observed")
        else:
            _check("WARN", "read_readme",
                   "introspective question did NOT trigger file_read README.md")

    if expect.get("should_touch_internet") is False:
        if web_fetch_urls or any(t == "web_search" for t, _ in tools_used):
            _check(
                "WARN", "internet_avoided",
                f"unexpectedly touched the internet "
                f"(web_fetch={len(web_fetch_urls)})",
            )
        else:
            _check("PASS", "internet_avoided",
                   "no internet activity for an introspective question")

    if expect.get("should_touch_internet") is True:
        if web_fetch_urls or any(t == "web_search" for t, _ in tools_used):
            _check("PASS", "internet_touched",
                   f"web_fetch={len(web_fetch_urls)}, "
                   f"web_search="
                   f"{sum(1 for t, _ in tools_used if t == 'web_search')}")
        else:
            _check(
                "WARN", "internet_touched",
                "expected internet activity but none observed",
            )

    min_fetches = expect.get("min_web_fetches")
    if min_fetches is not None:
        if len(web_fetch_urls) >= min_fetches:
            _check("PASS", "min_web_fetches",
                   f"web_fetch count={len(web_fetch_urls)} >= {min_fetches}")
        else:
            _check(
                "WARN", "min_web_fetches",
                f"web_fetch count={len(web_fetch_urls)} < {min_fetches}",
            )

    expected_hosts = expect.get("expected_hosts")
    if expected_hosts:
        missing = [h for h in expected_hosts if h not in host_counts]
        if missing:
            _check(
                "WARN", "expected_hosts",
                f"expected hosts NOT touched: {missing}",
            )
        else:
            _check("PASS", "expected_hosts",
                   f"all expected hosts touched: {expected_hosts}")

    if local_target_attempts:
        _check(
            "FAIL", "no_local_targets",
            f"tried to fetch LOCAL network URL(s): {local_target_attempts}",
        )
    else:
        _check("PASS", "no_local_targets",
               "no localhost / private-IP web_fetch attempts")

    if duplicates:
        # A SAME (tool, args) pair called more than once is the
        # textbook cycling pattern. We tolerate up to 1 dup for the
        # verify-replan path (planner re-runs the same web_fetch when
        # the previous one failed gracefully), but anything more is a
        # warning.
        worst = max(duplicates.values())
        if worst >= 3:
            _check(
                "FAIL", "no_cycling",
                f"a tool call repeated {worst} times: "
                f"{[(t, c) for (t, _), c in duplicates.items() if c == worst]}",
            )
        elif worst == 2:
            _check(
                "WARN", "no_cycling",
                f"a tool call repeated twice: "
                f"{[(t, c) for (t, _), c in duplicates.items() if c == 2]}",
            )
    else:
        _check("PASS", "no_cycling",
               "every (tool, args) pair appeared at most once")

    if expect.get("must_not_loop"):
        # Extra guard for impossible/ambiguous questions: the verify-
        # phase capped event SHOULD NOT fire, and the global cap
        # SHOULD NOT be hit. If either does, treat as a WARN —
        # graceful exits but the agent floundered.
        if replan_capped:
            _check(
                "WARN", "must_not_loop",
                "verify-replan hard cap was hit (graceful exit but flailing)",
            )
        elif attempts_used >= 3:
            _check(
                "WARN", "must_not_loop",
                f"attempts_used reached the global cap ({attempts_used})",
            )
        else:
            _check("PASS", "must_not_loop",
                   "exited within budget without hitting any cap")

    # --- bundle everything ------------------------------------------------
    return {
        "scenario": scenario["name"],
        "question": scenario["question"],
        "elapsed_s": round(elapsed_s, 2),
        "crashed": crashed,
        "answer_preview": (answer or "")[:280],
        "answer_chars": len(answer or ""),
        "attempts_used": attempts_used,
        "planner_calls": len(planner_events),
        "tool_calls_total": len(tool_calls),
        "tool_call_breakdown": dict(
            Counter(t for t, _ in tools_used)
        ),
        "duplicates": {
            f"{t} {args}": c for (t, args), c in duplicates.items()
        },
        "web_fetch_urls": web_fetch_urls,
        "web_hosts": dict(host_counts),
        "local_target_attempts": local_target_attempts,
        "replan_events": [
            {
                "phase": (e.get("payload") or {}).get("phase") or "tool",
                "triggers": (e.get("payload") or {}).get("triggers"),
                "unresolved_urls":
                    (e.get("payload") or {}).get("unresolved_urls"),
            }
            for e in replan_events
        ],
        "replan_noop_count": len(replan_noop),
        "replan_capped_count": len(replan_capped),
        "verifications": [
            {
                "phase": (e.get("payload") or {}).get("phase") or "synth",
                "verified": (e.get("payload") or {}).get("verified_chunks"),
                "unverified": (e.get("payload") or {}).get("unverified_chunks"),
                "unmatched":
                    (e.get("payload") or {}).get("cited_but_unmatched_chunks"),
            }
            for e in verifications
        ],
        "verifier_final": ver,
        "llm_usage": llm_summary,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Pretty-printer
# ---------------------------------------------------------------------------

def _print_report(rep: dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print(f"SCENARIO: {rep['scenario']}")
    print(f"QUESTION: {rep['question']}")
    print(
        f"  elapsed={rep['elapsed_s']}s   "
        f"attempts_used={rep['attempts_used']}   "
        f"planner_calls={rep['planner_calls']}   "
        f"tools_total={rep['tool_calls_total']}"
    )
    u = rep["llm_usage"]
    print(
        f"  llm: {u['provider']}/{u['model']}  "
        f"calls={u['call_count']}  "
        f"in={u['input_tokens']} out={u['output_tokens']} "
        f"total={u['total_tokens']}"
    )

    if rep["tool_call_breakdown"]:
        breakdown = ", ".join(
            f"{k}={v}" for k, v in rep["tool_call_breakdown"].items()
        )
        print(f"  tools breakdown: {breakdown}")

    if rep["web_fetch_urls"]:
        print(f"  web_fetch URLs ({len(rep['web_fetch_urls'])}):")
        for url in rep["web_fetch_urls"][:8]:
            print(f"    - {url}")
        if len(rep["web_fetch_urls"]) > 8:
            print(f"    ... +{len(rep['web_fetch_urls']) - 8} more")

    if rep["web_hosts"]:
        hosts = ", ".join(f"{h}({c})" for h, c in rep["web_hosts"].items())
        print(f"  web hosts: {hosts}")

    if rep["duplicates"]:
        print("  duplicate (tool, args) calls:")
        for k, c in rep["duplicates"].items():
            print(f"    [{c}x] {k}")

    if rep["replan_events"]:
        print(f"  replan events ({len(rep['replan_events'])}):")
        for r in rep["replan_events"]:
            urls = r.get("unresolved_urls") or []
            urls_note = f" urls={len(urls)}" if urls else ""
            print(f"    - phase={r['phase']} triggers={r['triggers']}{urls_note}")
    if rep["replan_noop_count"]:
        print(f"  replan_noop_count: {rep['replan_noop_count']}")
    if rep["replan_capped_count"]:
        print(f"  replan_capped_count: {rep['replan_capped_count']}")

    if rep["verifier_final"]:
        v = rep["verifier_final"]
        print(
            f"  verifier final: total={v['total_chunks']} "
            f"verified={v['verified_chunks']} "
            f"unverified={v['unverified_chunks']} "
            f"unmatched={v['cited_but_unmatched_chunks']} "
            f"self_decl={v['self_declared_chunks']} "
            f"struct={v['structural_chunks']} "
            f"disclaimer={v['disclaimer_set']}"
        )

    print("  answer preview:")
    preview = rep["answer_preview"].replace("\n", " ")
    print(f"    {preview}{'...' if rep['answer_chars'] > 280 else ''}")

    print("  checks:")
    for level, name, detail in rep["checks"]:
        marker = {"PASS": "[OK]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[level]
        print(f"    {marker:7} {name:25} {detail}")


def _print_grand_summary(reports: list[dict[str, Any]]) -> None:
    print()
    print("=" * 78)
    print("GRAND SUMMARY")
    print("=" * 78)
    fails = [
        (r["scenario"], n, d)
        for r in reports for (lvl, n, d) in r["checks"] if lvl == "FAIL"
    ]
    warns = [
        (r["scenario"], n, d)
        for r in reports for (lvl, n, d) in r["checks"] if lvl == "WARN"
    ]
    passes_total = sum(
        1 for r in reports for (lvl, _, _) in r["checks"] if lvl == "PASS"
    )
    total_tokens = sum(r["llm_usage"]["total_tokens"] for r in reports)
    total_calls = sum(r["llm_usage"]["call_count"] for r in reports)
    total_elapsed = sum(r["elapsed_s"] for r in reports)
    total_tool_calls = sum(r["tool_calls_total"] for r in reports)
    total_web_fetches = sum(len(r["web_fetch_urls"]) for r in reports)
    print(
        f"  scenarios={len(reports)}  "
        f"elapsed_total={round(total_elapsed, 1)}s  "
        f"llm_calls={total_calls}  "
        f"llm_tokens={total_tokens}"
    )
    print(
        f"  tool_calls_total={total_tool_calls}  "
        f"web_fetches_total={total_web_fetches}"
    )
    print(
        f"  checks: PASS={passes_total}  WARN={len(warns)}  FAIL={len(fails)}"
    )
    if fails:
        print()
        print("  FAILED CHECKS (must fix):")
        for sc, n, d in fails:
            print(f"    [{sc}] {n}: {d}")
    if warns:
        print()
        print("  WARNINGS (review):")
        for sc, n, d in warns:
            print(f"    [{sc}] {n}: {d}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("AUTONOMOUS AGENT — LIVE AUDIT")
    print("=" * 78)
    print(f"  workspace: {WORKSPACE}")
    print(f"  scenarios: {len(SCENARIOS)}")
    print()

    reports: list[dict[str, Any]] = []
    for sc in SCENARIOS:
        # Per-scenario ISOLATION. We build a FRESH agent for each
        # scenario so:
        #   * trace_id is unique per scenario -> JSONL events of one
        #     run can't pollute the audit view of the next;
        #   * `with_memory=False` skips persistent memory but
        #     working memory + artifacts wouldn't survive across
        #     `run()` either, so this is purely about the log file;
        #   * LLM usage counters start at zero naturally (the
        #     new client has fresh state).
        # The cost is a few extra constructor calls per batch run —
        # tiny next to the LLM round-trips themselves.
        agent = build_agent(workspace=WORKSPACE, with_memory=False)
        crashed = False
        answer = ""
        verification = None
        started = time.monotonic()
        try:
            answer = agent.run(user_question=sc["question"])
            verification = agent.last_verification
        except Exception:  # noqa: BLE001
            crashed = True
            answer = traceback.format_exc()[:1000]
        elapsed_s = time.monotonic() - started

        trace_id = getattr(agent.log, "trace_id", "")
        events = _load_events(trace_id) if trace_id else []
        llm_summary = agent.llm.usage_summary()

        rep = _analyse(
            scenario=sc, events=events, llm_summary=llm_summary,
            elapsed_s=elapsed_s, answer=answer,
            verification=verification, crashed=crashed,
        )
        reports.append(rep)
        _print_report(rep)

    _print_grand_summary(reports)
    print()
    fails = sum(
        1 for r in reports for (lvl, _, _) in r["checks"] if lvl == "FAIL"
    )
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
