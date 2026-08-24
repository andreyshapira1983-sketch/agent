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
| H-04 | 1997 | watchdog reset loop with no diagnosis | Mars Pathfinder postmortem (Reeves, 1997) | priority inversion blocked a high-priority task; the watchdog reset repeatedly; the craft looked alive and did no work | `recover_stuck` + the failure policy | drive claim→recover→claim in a loop and read `attempts` each cycle | **recovery increments `attempts` (1/3, 2/3, 3/3) and the third pass makes the row terminal `failed` — the loop cannot sustain itself** | ALREADY PROTECTED | the OTHER half of Pathfinder — looking alive while doing nothing — is MIR-135 and remains open |
| H-05 | 1985-87 | a fast path justified by a state that is not true | Therac-25 (Leveson & Turner, 1993) | a fast operator path skipped a transition the interlock depended on, and the console showed a state the machine was not in. The danger was the DIVERGENCE between what the system said about itself and what it was | `_rank_and_catalog_evidence`, the cheap-path skip branch | end-to-end run of «привет» with one persistent record; read the cheap-path flag and the chain size together | **flag set AND chain non-empty (one `memory` evidence) — the branch's stated premise «the chain is empty (no tools ran)» was false** | **LOCALLY REPRODUCED → FIXED (premise, not behaviour)** | see below |
| H-06 | 2005-2014 | crash consistency: temp+rename without a durability barrier | ext3/ext4 rename semantics; Pillai et al., «All File Systems Are Not Created Equal», OSDI 2014 | rename gives atomicity of the DIRECTORY ENTRY, not of the file's DATA. After a host crash the renamed file can be empty or partial | `core/state_integrity._atomic_write_lines`, and the lock discipline around every `*_unlocked` writer | (a) AST check that every `_save_unlocked` caller sits inside a lock; (b) leave a stale `.tmp` and read; (c) inspect for a barrier before `replace` | (a) **11 of 11 production callers locked, 0 unlocked**; (b) a stale `.tmp` neither breaks the read nor survives the next write; (c) **no `flush`, no `fsync`** | **(a),(b) ALREADY PROTECTED · (c) mechanism confirmed by inspection, effect NOT reproducible in-process → fixed anyway** | an empty state file after a power cut would read as a legitimately empty store, and per-row checksums cannot help — there is nothing left to check |
| H-07 | 2012 | wall-clock deadlines under a clock step | leap-second kernel livelock (2012); NTP step corrections; dead-CMOS boots | deadlines computed as `now - stamp` misbehave when the clock moves; monotonic clocks are immune but do not survive a restart | `provider_unhealthy` (120 min), `recover_stuck` (30 min), `reactivate_paused_checkpoints` (60 min / 72 h), approval TTL, grant expiry | recompute each deadline with `now` stepped backwards by an hour and by a year | **provider stays parked (True/True/True); `recover_stuck` reclaims 0 instead of 1** | **LOCALLY REPRODUCED — and every direction is fail-SAFE** | registered, deliberately NOT fixed; see below |
| H-08 | 2008 | a silently weakened entropy source that still looks random | Debian OpenSSL, CVE-2008-0166 | a cleanup patch removed the uninitialised-memory mix; key generation was predictable for **two years**, and nobody noticed because the output still had the right shape and no repeats | `core/ids.new_id`, used as the default for every `id` field | (a) 200 000 ids, count collisions; (b) seed `random` and check whether ids become reproducible | (a) **0 collisions**, `secrets.token_hex(16)`, 128 bits; (b) not reproducible — but **no test pinned the SOURCE**: format and uniqueness both survive a swap to `random` | ALREADY PROTECTED (source) · **pinning gap closed** | the two existing tests would have passed after the exact Debian-shaped downgrade |
| H-10 | 2019 | regex cost multiplied by attacker-supplied input | Cloudflare global outage, 2 July 2019 | one WAF regex with catastrophic backtracking consumed CPU across the edge. The lesson is not «regexes are dangerous» but «cost × input size, and the input comes from outside» | 155 compiled patterns in `core/`, and the fetched-page text they see | sweep all patterns against pathological inputs; then measure SCALING to tell backtracking from polynomial cost | **14 patterns slower than 50 ms; the worst three scale ×4 per doubling — quadratic, not exponential** (52 → 206 → 836 → 3381 ms over 1250 → 10000 chars) | ALREADY PROTECTED — **incidentally**, and now pinned | protection is `MAX_EXCERPT_CHARS = 800`, a memory-size cap, not a parse-cost decision |
| H-11 | 2012 | old state read by new code, where a default asserts something the past never held | Knight Capital, 1 Aug 2012 (SEC 34-70694) | a repurposed flag activated dead code on one of eight servers; the deploy was partial and nobody could tell which meaning was live | every typed loader over the live stores | round-trip 15 588 live rows through the envelope, then through the TYPED loaders, and diff | envelope: **0 rows changed**; typed: **0 values changed**, but **162 rows GAIN a defaulted field** — `occurrences=1` on 25 self-improvement rows and `decided_by='unattributed'` on 137 inbox items | **LOCALLY REPRODUCED (one of the two defaults) → FIXED** | `decided_by='unattributed'` is honest; `occurrences=1` was not |
| H-14 | 2024 | a configuration file that takes the whole system down | CrowdStrike Falcon channel file 291, 19 July 2024 | a kernel-level component parsed a config channel file and crashed millions of hosts. Config is an input as dangerous as the network | `config/*.json` against `build_agent` | corrupt each file four ways, with a CONTROL run on healthy files first | control survives; `budget_limits.json` and `model_registry.json` **kill the build** on every corruption; `model_catalog.json` survives (it has a fallback) | **LOCALLY REPRODUCED — and the direction is CORRECT** | fail-closed pinned; the compound with MIR-135 recorded |

---

## Running summary

*(as of 2026-08-23, first block: 1994-1999)*

| metric | count |
|---|---|
| classes examined | 11 |
| NOT APPLICABLE | 0 |
| ALREADY PROTECTED | 6 (H-02, H-03, H-04, H-06 a/b, H-08, H-10) |
| UNKNOWN | 0 |
| LOCALLY REPRODUCED | 5 (H-01, H-05, H-07, H-11, H-14) |
| fixes completed | 4 (H-01, H-05, H-06c, H-11) |
| **pinning gaps closed on already-correct behaviour** | 2 (H-08, H-10) |
| reproduced and deliberately NOT fixed | 2 (H-07 fail-safe; H-14 already fails in the correct direction) |
| queued, not yet run | the chronological list below |

**A pattern worth naming after nine classes.** Twice now the code was correct
and the *proof* was missing: H-08's entropy source and H-10's cost bound were
both protected by something no test named. Neither would have been found by
asking «is there a defect»; both were found by asking «what exactly is holding
this up, and would I notice if it moved».

**Blast-radius ranking of what reproduced.** H-01 highest: false state → false
success → learning from an incorrect result → later decisions on earlier
corruption. H-06c next, but only under host crash: an empty state file reads as
an empty store. H-05 and H-07 lowest: one was a false premise with correct
behaviour, the other fails safe in every measured direction.

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


### H-05 — the fix that deliberately changes nothing

| stage | evidence |
|---|---|
| external claim | Therac-25: the machine's self-report diverged from its state; the fast path was not itself the defect |
| local applicability | the cheap path skips the knowledge pipeline and source-registry catalogue, justified in a comment by the chain being empty |
| local reproduction | end-to-end on «привет» with one persistent record: `planner_cheap_path` fired **and** `evidence_collected` reported **1** evidence of kind `memory`. Memory is folded into the chain (`_fold_evidence_chain`, `core/loop.py:475`) BEFORE the catalogue decision (`:484`) |
| causal mechanism | the branch decided on the cheap-path FLAG and described a different condition — chain emptiness — that nothing checked |
| why the behaviour is still right | running the pipeline over a memory-only chain would bank the agent's own record as new knowledge. That is self-confirmation, and the doctrine already exists (MIR-046: one's own memory is not an independent witness). **The skip is correct; only its reason was false** |
| minimal fix | the condition now tests what it claims: `cheap_path_active and not chain_has_tool_evidence(chain)`. On the cheap path no tool evidence exists by construction, so behaviour is unchanged and the premise became true |
| regression proof | `tests/test_the_cheap_path_states_a_true_premise.py` — memory-only and user-explicit chains stay skippable, any tool-derived kind does not, and the branch must not return to deciding on the flag alone. Break-tested |
| two test corrections, both recorded | my own new assertion first banned the PHRASE «the chain is empty» and failed on the comment that quotes it as history — banning the citation of a past error erases its explanation, so it now pins the logged reason instead. And an existing fixture matched the literal `if cheap_path_active:`; its three real assertions (pipeline not called, skip event present, ranking preserved) carry the invariant, so the literal was repointed rather than the invariant weakened |

**Autonomous blast radius: low, and stated as low.** No verdict changed and no
answer changed. What was closed is a divergence between a stated premise and a
checked condition — the specific thing that makes the NEXT change dangerous,
because the next reader would have trusted the comment.


### H-07 — reproduced, fail-safe, and deliberately left alone

Wall-clock arithmetic is **correct** for these deadlines and monotonic time
would be wrong: a cooldown, a TTL and a grant expiry must survive a process
restart, and a monotonic clock resets with the process. So the finding is not
«should have used monotonic».

What a backward step actually does, measured: an unhealthy provider **stays**
parked, and `recover_stuck` reclaims **nothing** instead of reclaiming a live
task. Both directions are conservative — the system never double-executes and
never un-parks a dead key because the clock moved. The cost is a **silent
stall**: with the clock an hour behind (a resumed VM, a dead CMOS battery
booting at 2000-01-01, an NTP step), every cooldown is frozen until wall time
catches up, and nothing says so.

**No fix, on purpose.** The reproduced failure does not demonstrate that new
machinery is necessary: clamping a negative age changes no outcome, and making
the deadlines aggressive on a negative age would re-open the double-execution
class that MIR-033 measured closed. What is recorded instead is the one
condition under which it would matter — a backward step lasting hours during an
unattended week freezes cooldowns and orphan recovery — so a future stall has a
named suspect instead of a mystery.

### H-06 — the barrier that cannot be tested by its effect

The lock discipline and the stale-`.tmp` behaviour are genuinely protected, and
both were measured rather than assumed. The third part is different: the
absence of `fsync` is only observable on a **host crash or power loss**, which
no in-process test can produce. The barrier was added anyway — measured cost
+1.3–1.7 ms per write at 142 / 1000 / 5155 rows against a tick that spends
seconds in provider calls — and the test pins the **presence and the order** of
the barrier, saying plainly that it does not pin the effect. That distinction is
the honest form of «mechanism confirmed, effect not reproduced».


### H-10 — protected by something that was not protecting it on purpose

The quadratic patterns are real: `_STAT_TRIGGER_RE` costs 3.4 seconds on a
10 000-character digit run. What keeps that unreachable is `make_evidence`
truncating every excerpt to **800 characters**, so anything running after
evidence creation sees at most that — worst case **7–21 ms**.

That cap exists for memory size, not for parse cost. It protects this by
coincidence, which means a future change that raises it for «fuller excerpts»
would look harmless and quadruple the cost per doubling. So the incidental
protection is now **explicit**: `tests/test_a_quadratic_regex_is_bounded_by_the_excerpt_cap.py`
pins the worst case at the current cap and pins that truncation is actually
applied. Break-tested by raising the cap 4× — two of three tests redden.

### H-08 — the two tests that would have passed the Debian downgrade

`new_id` uses `secrets.token_hex(16)` and 200 000 draws produced no collisions,
so the source is right. The finding is about the **pinning**, and it is the
Debian shape exactly: the existing tests check the FORMAT (`^[a-z]+_[0-9a-f]{32}$`)
and UNIQUENESS in a tight loop, and a swap to `random.getrandbits(128)` passes
both. The 2008 patch survived two years for the same reason — the output kept
looking right.

Closed behaviourally rather than by grepping for the word `secrets`: seed
`random`, draw ids, re-seed, draw again, and require that they differ. Break-
tested by performing the downgrade — the new test reddens, the old two do not.


### H-11 — the default that claimed a past it never had

The envelope round-trip was clean and the typed round-trip changed no value, so
the dangerous Knight direction — a field whose MEANING silently differs — is
absent. What the probe did surface is the subtler half: **a new field's default
asserts something about rows written before it existed.**

Two defaults, and they are not equal:

* `decided_by = 'unattributed'` on 137 inbox items — **honest**. "We do not know
  who decided" is exactly what the old rows contain.
* `occurrences = 1` on 25 self-improvement rows — **not honest, and it was mine**, added
  2026-08-22 for MIR-035. Those rows predate the counter, so their true count is
  unknown; and MIR-035 measured **13 copies of one signal class**, i.e. exactly
  those rows are the ones known to have recurred. The mechanism built to surface
  magnitude was reporting `1` for the records that motivated it.

Fixed without a migration and without a new field, because the count already IS
a lower bound — it starts when the field was introduced. The consumer now says
`seen>=N` instead of `seen=Nx`, and the field carries that in its own comment.
Pinned by a test that strips `occurrences` from a row the way the store hands it
over, and requires the `>=`.

### H-14 — reproduced, and the behaviour is right

With a proved control (healthy configs build fine), corrupting
`budget_limits.json` or `model_registry.json` by truncation, garbage or
emptying kills `build_agent` every time. `model_catalog.json` survives — it has
a fallback.

**This is not fixed by making the parser lenient**, and that is the finding.
For a spend limit, refusing to run IS the correct direction: a lenient reader
("could not read it, so assume no limits") turns file corruption into unbounded
spending. What is pinned is therefore the DIRECTION of the failure — closed,
never open — break-tested by making the parser return `{}` instead of raising.

**The compound belongs to MIR-135 and is recorded there, not here.** The crash
happens at BUILD time, i.e. after the `tick_start` heartbeat is written, so a
daemon with a corrupt config would die every tick while reading `alive`. That
is a second defect meeting this one, and it stays with the entry that owns it.

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
