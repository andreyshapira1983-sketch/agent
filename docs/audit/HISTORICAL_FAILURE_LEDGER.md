# Historical failure ledger — an adversarial curriculum from other people's accidents

**Purpose.** Not a survey. Decades of other engineers' outages, defects, CVEs and
postmortems are used here as a source of **falsifiable hypotheses** against this
agent. A historical report is never evidence that this repository has the
defect; only a local reproduction is.

**Evidence stages, never collapsed.** `external claim` → `local applicability` →
`local reproduction` → `causal mechanism` → `fix` → `regression proof`.
"No consumer found" is not "no consumer exists". "Tests stayed green" is not
"semantically correct".

**Classification.** `NOT APPLICABLE` · `ALREADY PROTECTED` · `UNKNOWN` ·
`LOCALLY REPRODUCED`.

**Method note.** The sweep runs chronologically from 1994. Recency is not
relevance: a 1996 avionics data-bus failure may transfer better than a 2026
agent paper. Finding a defect interrupts the sweep only if it is a launch
blocker; otherwise it is registered and the queue resumes.

---

## Ledger

| # | year | failure class | source | mechanism | local target | probe | result | status | follow-up |
|---|---|---|---|---|---|---|---|---|---|
| H-01 | 1996 | diagnostic payload consumed as valid data | Ariane 501 Inquiry Board (Lions, 1996) | after the IRS faulted it put a **diagnostic bit pattern** on the databus; the flight computer read it as flight data and steered on it. The sensor failed honestly — the indistinguishability did the damage | `evidence_from_tool_result`, `web_fetch` branch | feed bodies that are error pages / login walls / captchas through the evidence factory, then verify a claim citing them | **soft-404 became `web_page` evidence at 0.75, and a Russian claim citing an English error page came back `verified`** | **LOCALLY REPRODUCED → FIXED** | see below |
| H-02 | 1999 | unit mismatch across a module boundary | Mars Climate Orbiter MIB (NASA, 1999) | one side produced pound-seconds, the other consumed newton-seconds; no end-to-end check ever compared them | every `*_seconds`/`*_minutes`/`*_hours`/`*_days`/`*_ms` parameter in `core`, `app`, `cli`, `agent_tick` | (a) AST sweep for a value whose name carries a DIFFERENT unit than the parameter; (b) literals and defaults whose MAGNITUDE is implausible for the declared unit | 76 unit-carrying parameters swept; **0 name mismatches, 0 implausible magnitudes** | ALREADY PROTECTED (naming convention holds) | the deeper MCO lesson — nobody compares end to end — is not disproved by this; a semantic double-conversion would pass both probes |
| H-03 | 1994 | a computing unit silently wrong on rare inputs | Intel Pentium FDIV erratum; Nicely (1994) | the divider was wrong on a sparse input set; nothing recomputed independently, so it stayed invisible until an outsider checked | `core/claim_arithmetic.evaluate` — the gate that COMPUTES verdicts | differential test against independent recomputation, random inputs, both shapes taught on 2026-08-23 | **1400 cases, 0 disagreements — and coverage proved: 700 `supports` + 700 `refutes`, ZERO `silent`** | ALREADY PROTECTED | probe kept; extend when new shapes are taught |
| H-04 | 1997 | watchdog reset loop with no diagnosis | Mars Pathfinder flight-software postmortem (Reeves, 1997) | priority inversion blocked a high-priority task; the watchdog reset repeatedly; the craft looked alive and did no work | `recover_stuck`, `reactivate_resumable_work`, daemon heartbeat | can recovery itself loop, and does a recovery record why it fired? | not yet run | queued | overlaps MIR-135 (a crash-looping daemon reports `alive`) |
| H-05 | 1985-87 | operator-invisible state + fast-path race | Therac-25 (Leveson & Turner, 1993) | a fast operator path skipped a state transition the interlock depended on; the console showed a state the machine was not in | the cheap path (`can_skip_planner`) and the gates it bypasses | enumerate gates on the slow path and check which are absent on the cheap path | not yet run | queued | MIR-020's audit covered routing, not gate parity |

---

## Running summary

*(as of 2026-08-23, first block: 1994-1999)*

| metric | count |
|---|---|
| classes examined | 3 |
| NOT APPLICABLE | 0 |
| ALREADY PROTECTED | 2 (H-02, H-03) |
| UNKNOWN | 0 |
| LOCALLY REPRODUCED | 1 (H-01) |
| fixes completed | 1 (H-01) |
| queued, not yet run | 2 (H-04, H-05) + the chronological list below |

**Highest autonomous blast radius so far — H-01.** It is not a display defect.
An error page becoming evidence lets the agent (a) **observe false state**,
(b) **falsely report success** — a claim comes back `verified`, (c) **learn from
an incorrect result**, since the knowledge pipeline banks verified claims into
durable memory, and (d) **make later decisions on earlier corruption**. It also
compounds MIR-140: the verify-replan loop FETCHES the cited URL, so a page that
has since become a login wall would resolve the citation and RAISE acceptance.

### H-01 — the full chain, stage by stage

| stage | evidence |
|---|---|
| external claim | Ariane 501 board: diagnostic pattern on the databus read as flight data |
| local applicability | our `web_fetch` returns HTTP 200 for soft-404s, login walls, captchas, parked domains |
| local reproduction | such a body became `web_page` evidence, confidence 0.75 |
| causal mechanism | the factory validated the SHAPE of the payload (`dict` with non-empty `text`) and never its NATURE |
| downstream consequence | a Russian claim citing an English error page verified — because the script guard (MIR-144) correctly silences the topic gate across languages, and this agent reads English and answers in Russian, so the hole opened on the most common path |
| minimal fix | reject at the PRODUCER boundary: an error-page body does not become evidence. Not at the claim side — the verifier's gate ladder already owns claim verdicts, and a second judge there would argue with the first |
| regression proof | `tests/test_an_error_page_is_not_evidence.py` — 8 stub bodies (RU+EN), a real page, a LONG article about errors, a SHORT article about errors, and the end-to-end verdict. Both halves break-tested |
| a tuning caught and undone | the first rule judged by LENGTH alone, and a genuine soft-404 article came in at 581 chars — i.e. the threshold was fitted to the fixture. The rule now judges SHAPE: short **and** at most three sentences, so the marker has to BE the content rather than appear in it. A second, shorter article pins that the length rule cannot return |

## Queue (chronological, not yet reached)

2000s: Y2K-style epoch rollovers · TCP incast · Byzantine clock skew · MD5
collisions in trust decisions · SQL injection as parser differential ·
Therac-class interlock bypass in schedulers.

2004-2010: filesystem crash-consistency (ext3 ordered vs writeback, fsync
semantics) · ZFS end-to-end checksums vs silent bit rot · Dynamo eventual
consistency and read repair · Paxos/Raft split brain · memcached stampede ·
Debian OpenSSL entropy CVE-2008-0166.

2011-2016: leap second kernel livelock (2012) · AWS EBS re-mirroring storm
(2011) · GitHub MySQL failover (2012) · Knight Capital deploy skew (2012) ·
Heartbleed (2014) · Shellshock (2014) · GHOST · Azure leap-day cert (2012) ·
Cloudflare regex catastrophic backtracking (2019, queued here as a class).

2017-2021: GitLab database deletion (2017) · Cloudflare Cloudbleed (2017) ·
Meltdown/Spectre (2018) · Facebook BGP withdrawal (2021) · log4shell (2021) ·
SolarWinds supply chain (2020) · Rowhammer as data corruption class.

2022-2026: Atlassian multi-day outage (2022) · Rogers routing (2022) ·
CrowdStrike channel-file parser (2024) · xz-utils backdoor (2024) · agent
runtime silent-failure taxonomies (2026, already partly used in MIR-133..147).
