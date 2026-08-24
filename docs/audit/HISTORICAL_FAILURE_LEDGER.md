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
| H-13 | 2021 | data interpreted by a formatting layer | log4shell, CVE-2021-44228 | a logging library resolved `${jndi:...}` inside data it was merely formatting, turning a log line into remote code execution | every non-literal format template; then the adjacent reachable form — a topic interpolated next to a `site:` operator | (a) AST sweep for format templates that are not literals; (b) craft topics that escape the `site:` restriction; (c) run classic domain-gate bypasses | (a) **2 non-literal templates, both module constants — not reachable from outside**; (b) escape into the QUERY works; (c) the second layer holds — 6 of 7, incl. suffix and user-info bypasses | ALREADY PROTECTED (layered) · **one false rejection found and fixed** | the gate compared `netloc`, so a legitimate port was rejected |
| H-15 | 2017 | adjacent data leaking into output | Cloudbleed (Cloudflare, Feb 2017) | an HTML-parser bug emitted NEIGHBOURING memory into responses; other users' cookies and messages ended up in search-engine caches | `core/secret_scanner` / `redact_dlp_text`, which cleans logs, receipts and quarantine files | run seven real secret shapes through the scanner and check what survives verbatim | **two passed through: the AWS SECRET beside a caught `AKIA…`, and `https://user:pass@host` — nothing at all** | **LOCALLY REPRODUCED → FIXED** | one limit kept deliberately and pinned as a limit |
| H-12 | 2017 | backups that exist and were never restored | GitLab.com database incident, 31 Jan 2017 (public postmortem) | the primary database directory was removed during recovery, and of **five** configured backup/replication methods **none** worked — some misconfigured, some untested for a year. A backup nobody has restored from is an assumption | the nine `*.bak` files in `data/` and the migrations that write them | run every backup through the REAL state loader, then compare identity against the live store using each store's own identity field | **9 of 9 load.** But identity comparison shows three are REPLACEMENTS, not repairs — `persistent_memory.jsonl.20260731…` shares **0 of 814** rows with the live store | **ALREADY PROTECTED (readability) · UNKNOWN → now measured (what each backup IS)** | drill made repeatable; see below |
| H-16 | 2024 | what RUNS differs from what was reviewed | xz-utils backdoor, CVE-2024-3094 (Mar 2024) | the payload shipped in release tarballs and was absent from the git repo; the build system activated it. The precondition is the gap between reviewed source and running artefact, not malice in a dependency | `requirements.lock`, `requirements.txt`, and the installed environment | (a) does every locked package carry a hash; (b) does the INSTALLED set match the lock | (a) **36 packages, 662 sha256 hashes, none missing**; (b) **2 packages drift: `anthropic` locked 0.102.0 / installed 0.121.0, `click` 8.4.1 / 8.4.2** | **LOCALLY REPRODUCED (drift) — reported, deliberately not "fixed"** | hashes protect an install that goes through them and say nothing about one that went around |
| H-17 | 2021 | an action inside your own authority removes the path that repairs you | Facebook/Meta global outage, 4 Oct 2021 | a routine capacity check withdrew the BGP routes, taking down the service AND the tools needed to fix it — internal DNS, remote access, reportedly even door badges | `_ALLOWED_CODE_DIRS` in the self-apply lane, against the launcher, bootstrap, installers and ratchets | ask the risk classifier whether the agent may edit each of those paths | **`agent_tick.py`, `main.py`, `app/`, `scripts/`, `.git/`, and every config are OUT of reach** | ALREADY PROTECTED — and now pinned | the property rests on one four-string tuple |
| H-19 | 2018-2015 | information reaching a channel not built to carry it | Meltdown / Spectre (2018); Rowhammer (Kim et al., 2014) | a side channel leaks what no interface exposed — cache timing, adjacent DRAM rows. The general shape: data crosses a boundary through a path nobody designed as a path | every durable surface a fetched page can reach: chain log payload, knowledge pipeline, memory write policy, source registry, the answer itself | plant a real secret in a fetched page and follow it through each surface | **chain log payload carries no excerpt; knowledge pipeline refuses; memory write policy rejects all three shapes; outbound redaction catches all three; 0 hits across 5 151 live source-registry rows** | ALREADY PROTECTED, in depth | yesterday's H-15 patterns propagated here on their own |
| H-18 | 2022 | a repair path that does not scale to the size of the incident | Atlassian, April 2022 (883 sites deleted; public incident review) | restores were per-tenant and largely manual, so recovery ran for up to two weeks. The damage was instant and the repair was serial | the approval inbox against a week of unattended ticks | simulate 336 ticks (48/day x 7) and read both the file and the queue | **two independent bounds, and they are not interchangeable**: the dedup key collapses IDENTICAL proposals to one row (612 bytes), and the in-flight gate refuses while any Stage-A item is `pending` OR `approved`-but-unexecuted | ALREADY PROTECTED — and now pinned | the dedup key alone would do nothing against 336 DIFFERENT proposals |
| H-20 | 2026 | commitment drift vs binding drift | «The LLM Proposes, the Executive Disposes» (4 Aug 2026), supplied by the operator | two different losses: the agent stops carrying the goal at all, or it keeps the goal and loses its link to the concrete referent. Their ablation raised goal-abandonment 0.00 → 1.00 when the external commitment store was removed, while binding-error stayed 0.00 | the durable task queue and `BestNextAction` | (a) park a checkpoint, reopen the store in a fresh object, read the goal; (b) ask the decision object for the file its own reason named | (a) **commitment survives verbatim** across a store reopen; (b) **binding was lost: `_PY_TARGET_RE` matched the filename, classified the goal as engineering, and DISCARDED the match** | **(a) ALREADY PROTECTED · (b) LOCALLY REPRODUCED → FIXED** | our binding-error was the axis their ablation kept at zero |
| H-21 | 1985- | a state machine that accepts a transition its diagram does not have | Therac-25 (Leveson & Turner, 1993) and the wider control-system literature | the state changes by a path the design never drew, and every later decision reasons about a world that did not happen | the task queue's terminal statuses | drive the FULL transition matrix over settled tasks, not one case | **only `mark_running` was guarded. `done→failed`, `done→cancelled`, `failed→done`, `failed→cancelled`, `cancelled→done`, `cancelled→failed` were all accepted** | **LOCALLY REPRODUCED → FIXED** | `failed→done` is literally «falsely report success» |
| H-25 | 1999/2000 | two conventions with no end-to-end check, in the TIME domain | Mars Climate Orbiter (H-02) restated for timestamps; the Y2K epoch family | a value carries no unit, each side assumes its own, and nothing ever compares them | every `fromisoformat` site in `core/` | (a) count naive stamps in live state; (b) parse a naive stamp and read the instant it becomes | (a) **12 472 stamps, all timezone-aware, zero naive**; (b) **a naive stamp was read as LOCAL time — 10:00 became 07:00Z on this machine, a silent three-hour shift** | **mechanism live, data clean → FIXED at the two silent sites** | 7 sites did not normalise; 5 raise loudly, 2 shifted silently |
| H-26 | 2005- | parser differential: two gates read one input and disagree | HTTP request-smuggling literature; the wider filter-vs-consumer family | the danger is not strictness or leniency but that the disagreement is undeclared, so a value passes the gate that judges it one way and reaches the consumer that judges it another | `secret_scanner.scan` vs `contains_secret(keywords)` vs `redact_dlp_text` vs the two durable stores | run one text through every boundary and compare verdicts | **«My password is hunter2»: 0 patterns, keyword TRUE, persistent memory REJECTS, redaction cuts nothing, and the episodic store keeps it verbatim** | **differential real, NOT reproduced in data → declared, not equalised** | 0 hits of either class across 6 426 live rows |
| H-27 | 1970s- | an external effect happens, its accounting does not | double-entry bookkeeping; two-phase commit; the duplicate-charge family in payment postmortems | the money moved and the write that was supposed to count it never ran, or ran through a path that never reached the ledger; the cap then guards a number that is not the spend | `assert_can_start` / `record` around the paid model call, and every construction site of `ModelUsageLedger` | (a) interrupt between reserve and record and read what survives; (b) reconcile live `llm_calls` reservations against live usage rows | (a) the CALL count survives, tokens and cost do not — realistic loss over an unattended week ~0.1-0.4% of the weekly cap, so no machinery is owed; (b) **1364 reservations against 1379 usage rows — 15 paid calls, all openai, all on 19-20 Aug, invisible to the daily and weekly cap** | **LOCALLY REPRODUCED → FIXED (b); (a) measured and declined** | see below |
| H-28 | 2004- | the same expensive work paid for twice | memcached cache stampede; Knight Capital deploy skew (2012) as the duplicate-execution family | N workers ask one expensive question at once, or one path runs the same costly action again, and the accounting shows one | the campaign cycle (`core/campaign.py`), the replan loop, and the live `model_usage.jsonl` | (a) cluster live successful calls by identical (role, model, input_tokens) inside 10 minutes; (b) read the gap distribution; (c) attribute campaign spend by outcome | (a) **86 repeats, 8.7% of all successful calls, clusters up to 8**; (b) **median gap 70 s, only 2 pairs under 10 s — a cadence, not a retry loop, and replan explains 10 events, not 86**; (c) **idle 30, repeat 39, blocked 82 all genuinely zero, with `completed` at 386 calls / 2871 units as the control** | **ALREADY PROTECTED at the campaign boundary; prompt identity UNKNOWN and unauditable by design** | see below |
| H-29 | 2005- | corruption detected, and silently LESS data returned | ZFS end-to-end checksums vs silent bit rot; the wider detect-but-don't-tell family | the checksum catches the bad block and the reader still gets a short answer it cannot distinguish from a complete one | `read_state_jsonl` quarantine path, and every store that BOUNDS something | exhaust a 3-call daily cap, corrupt one budget row, re-read, and ask for a fourth call | **the fourth call is ALLOWED — one corrupted row restores spending room, with no warning, no log and no reader of `.quarantine` anywhere in the repo** | **LOCALLY REPRODUCED → the silence closed, the decision left to the operator** | live quarantine empty across all stores in four weeks |
| H-30 | 2008 | identifiers unique only in appearance | Debian OpenSSL entropy CVE-2008-0166; the wider weak-source family | the generator looks right and the key space is tiny, so collisions arrive by design rather than by chance | `core/ids.new_id` and every minting site in `core`, `app`, `cli`, `api`, `tools`, `agent_tick` | (a) AST sweep for `random.*` / `uuid1` at any minting site; (b) count real id collisions across every live store | (a) **0 weak sources against 64 strong minting sites, detector proved on `random.randint` and `uuid.uuid1`**; (b) **2439 ids, and both apparent collisions are references, not keys — 254 versions of one profile, 3 outcomes of one approval** | ALREADY PROTECTED (`secrets.token_hex(16)`, 128 bits) | the collision count needed decomposition before it meant anything |
| H-31 | 2012 | old code meets new data and ACTS on it | Knight Capital deploy skew (2012) | part of the fleet ran the new build and part the old; the loss came not from the skew but from the old half acting on data it misread | `importlib` use in the running process, the self-apply lane's clean-tree gate, and the shared state format `INTEGRITY_MARKER` | (a) sweep for live module reloading; (b) write a row from a FUTURE format and read it with today's reader | (a) **no `importlib.reload` anywhere — one process, one code version**; (b) **the future row is not ignored: it is quarantined and the LIVE FILE is rewritten without it** | **mechanism real, not currently reachable (only v1 exists) → tripwire, not machinery** | the destruction half is what makes a future bump dangerous |
| H-32 | 2011 | recovery itself becomes the load | AWS EBS re-mirroring storm (2011); the wider retry-avalanche family | the repair path is unthrottled, so everything that was waiting fires at once and the recovery outlasts the outage | `reactivate_paused_checkpoints`, the tick's drain loop, and every producer of `pending` rows | (a) read the revival bound; (b) read the drain bound; (c) enumerate every producer of pending rows | (a) **batch of 3, oldest first, with the EBS reasoning written in the code already**; (b) **the drain is UNBOUNDED by count and the tick has no time budget — only the money caps hold it**; (c) **two producers only: a human CLI add, and one row per due schedule, of which zero are registered** | ALREADY PROTECTED, by producer enumeration rather than by assumption | the residual is named below |
| H-33 | 2021 | the protective mechanism removes protection when IT fails | Facebook BGP withdrawal (2021); the fail-open family | the thing meant to keep the system safe took the system off the map when it failed | `.env`, the provider chain, and `config/budget_limits.json` | (a) run with no `.env`; (b) run with no keys; (c) run with the limits file absent | (a) **proceeds on defaults, does not raise**; (b) **the chain ends at the local provider and fails with a connection error — a stop, not silent garbage**; (c) **NO CAP AT ALL: 500 reservations allowed in a row, silently, while the same file CORRUPTED raises** | **LOCALLY REPRODUCED → FIXED at the process entry** | the placement took three attempts, and both wrong ones were red for good reasons |
| H-34 | 2014 | the boundary is checked for some shapes of input, not all | Heartbleed (2014); the wider partial-boundary family | the reply carried more than was asked because one request shape skipped the length check | `redact_payload` and the three surfaces §7 declares safe | push a secret through every payload shape the logger accepts, then read the FILE | **a secret inside a set or inside bytes reached `logs/*.jsonl` raw — the logger accepts both (it stringifies) and the traversal did not enter them** | **LOCALLY REPRODUCED → FIXED for sets and bytes; the dict-key decision upheld after testing its premise** | 0 occurrences live: no sets, no bytes in 6489 rows; 0 key-shaped strings in 1269 files |

---

## Running summary

*(as of 2026-08-23, first block: 1994-1999)*

| metric | count |
|---|---|
| classes examined | 22 |
| NOT APPLICABLE | 0 |
| ALREADY PROTECTED | 12 (H-18, H-02, H-03, H-04, H-06 a/b, H-08, H-10, H-12 readability, H-13, H-16 lock, H-17, H-19) |
| UNKNOWN → measured | 2 (H-12 what each backup restores; H-26 the declared differential) |
| LOCALLY REPRODUCED | 9 (H-01, H-05, H-07, H-11, H-14, H-15, H-16 drift, H-20 binding, H-21) |
| fixes completed | 9 (H-25, H-01, H-05, H-06c, H-11, H-13 false rejection, H-15, H-20, H-21*) |
| *fixes without a mutation probe | 1 (H-21 — probe interrupted) |
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


### H-13 — the class does not reach us, but probing it found a false rejection

The log4shell mechanism proper is absent: the only two non-literal format
templates are module constants, so no outside data becomes a template. The
adjacent form is reachable — a topic is interpolated beside a `site:` operator,
and a crafted topic does reach the query string intact. The **second** layer is
what protects, and it holds against the classic bypasses: suffix confusion
(`wikipedia.org.evil.example`), user-info (`wikipedia.org@evil.example`), and
the domain appearing only in a query parameter.

What the probe found was the opposite of a hole: `_domain` read `netloc`, which
carries user-info **and the port**, so a legitimate `https://wikipedia.org:8443/x`
was **rejected**. It now reads `hostname`, which fixes the port and makes the
user-info case correct **by construction** rather than by the accident of a
string not ending in the allowed domain. Eight cases pinned, bypasses included.

### H-15 — the dangerous half of a pair

Seven secret shapes measured; two survived redaction verbatim:

* the **AWS secret** sitting beside an `AKIA…` that WAS caught — and the caught
  half is the public one;
* `https://user:p4ssw0rd@example.org/x`, where nothing matched at all, although
  the neighbouring `mongodb-uri` rule already knew this exact shape for one
  scheme. Fetching URLs is the agent's daily work.

Both are reachable through `redact_dlp_text`, which cleans logs, receipts and
quarantine files — the artefacts that OUTLIVE the run.

Two patterns added, and five clean strings pinned as must-stay-silent (a plain
URL, a URL with a port, a bare sha, a base64 hash, a colon inside a path),
because a redactor that quarantines innocent content is its own defect.

**A measured limit, pinned AS a limit.** A bare 40-character AWS secret with no
label is still not caught, deliberately: such a string is indistinguishable from
a hash, and a length-only rule would fire on every sha. The test asserts that it
stays uncaught, so that a future length-based rule reddens and has to justify
itself.

### A process failure of mine, recorded

Break-testing these two patterns, I reverted the break with `git checkout --`
while the FIX was still uncommitted, and destroyed both patterns. My own memory
carries this exact rule — commit first, or revert with the inverse edit — and I
broke it anyway. The patterns were restored from the scratch script and the
break-tests were redone by inverse edit. Recorded because the second-order
lesson is that a break-test procedure which can delete the fix is itself a
defect in the method, not just a slip.


### H-12 — the backups load; what they MEAN was the unmeasured part

GitLab's failure was untested backups. Ours are testable and all nine pass:
every `*.bak` in `data/` loads through the same `read_state_jsonl` that reads
the live files, with checksums and quarantine active. That is the drill GitLab
lacked, and it is now green.

The finding is one layer along. A backup that loads still answers nothing about
**what restoring it would do**, and the identity comparison splits them:

| backup | rows | restoring it would be |
|---|---|---|
| `runtime_tasks…20260822` | 21 | a repair — 21 of 21 shared |
| `persistent_memory…pre-injection-cleanup` | 94 | a repair — 89 of 94 |
| `episodic_memory…pre-gate-wait-demotion` | 200 | a repair — 142 of 200 |
| `self_improvement_issues…pre-echo-collapse` | 106 | **a replacement** — 16 of 106 |
| `episodic_memory…20260730` | 22 | **a replacement** — 10 of 22 |
| `persistent_memory…20260731` | 814 | **a replacement** — **0 of 814** |

The last one is the sharp case: 814 rows sharing nothing with today's 125.
Restoring it during an incident would not repair the store, it would replace a
month of state — and it sits in the same directory, with the same suffix,
looking exactly like the backups that WOULD repair.

Nothing is deleted and no policy is added: what was missing is the reading, so
`scripts/restore_drill.py` produces it on demand, and
`tests/test_every_backup_can_be_restored.py` keeps the readability half green
with a control that proves the drill can still catch a corrupt backup — a green
drill over zero backups would mean nothing.

**Probe artefacts caught before they became findings.** The first comparison
used `id` for every store and reported «0 unique on both sides» for
`self_improvement_issues` and `source_registry`. Neither has an `id` field —
the sets were `{None}` on both sides. The drill now picks the identity field
per store and says «unknown» where there is none, instead of inventing a
number.


### H-17 — the outer boundary holds, and it rests on four strings

The agent cannot edit what starts it. `_ALLOWED_CODE_DIRS = ("core", "cli",
"tools", "tests")` leaves `agent_tick.py`, `main.py`, `app/bootstrap.py`, the
whole of `scripts/` (installer, restore drill, ratchet baselines) and every
config outside; `.git/` is separately denylisted, so history — and with it the
ability to revert — is out of reach too. That is Facebook's lesson honoured at
the outer layer.

It is pinned now because of how thin the guarantee is: adding `app` or
`scripts` to that tuple would read as a harmless widening («let it fix its own
scripts too»), the full battery would not notice, and the agent would gain the
right to rewrite its own launch. Thirteen paths are asserted out of reach and
five ordinary ones asserted still editable, so the ban cannot quietly become
either wider or narrower. Break-tested by adding the two directories — five
cases redden.

**What this does NOT claim.** `core/self_apply_lane.py` IS editable, so the
agent can modify the lane that applies its own patches. That is MIR-139 and it
stays open. This entry covers only the outer contour: the paths whose change
would remove the ability to start and to roll back.

### H-16 — the lock is complete; the environment left it anyway

Two questions, two different answers, kept apart.

The lock itself is sound: 36 packages, 662 `--hash=sha256:` entries, and not one
package pinned by version alone. A version-only pin would accept any archive
bearing that number, which is precisely the reviewed-vs-running gap xz used, so
that property is now pinned by a test.

The environment is another matter: **`anthropic` is locked at 0.102.0 and
installed at 0.121.0**, nineteen minor versions along, and `click` drifts by a
patch. Hashes protect an installation that goes THROUGH them; they are silent
about one that went around. Nothing was comparing the two, so the drift was
invisible — and the code that would run for an unattended week is not the code
the lock, a fresh install, or CI describes.

**RESOLVED 2026-08-24 on the operator's word: the lock was updated to the
installed set**, which lifts the standing «hash-locks untouched» constraint for
this named scope only. Two entries changed, thirty-four left byte-identical —
regenerating the whole lock would have churned transitive pins nobody asked to
move. Digests came from PyPI's metadata API, and then the lock lines themselves
were **verified by a real download under hash checking**: pip fetched both
distributions and accepted them.

That verification was then PROVED SENSITIVE, and the first attempt at proving it
was wrong. Corrupting one of `anthropic`'s two hashes still succeeded — pip
legitimately fell back to the sdist, whose hash matched — which almost read as
«the check does nothing». Corrupting BOTH produces
«THESE PACKAGES DO NOT MATCH THE HASHES» and exit 1. Drift is now 0 of 36.

The distinction the entry was built on still stands for everything else:
installing to close a gap changes the agent's working environment, and
dependencies are the operator's to move. `scripts/dependency_drift.py` prints the comparison and
exits 1; the completeness of the lock is a code property and is tested, while
the drift is a machine property and is not — a suite that reddens because
someone installed a package on their laptop would be a false rejection.


### H-19 — five surfaces, five separate answers

A secret planted in a fetched page was followed rather than assumed, because
«the scanner exists» is not «the scanner is on this path»:

| surface | result |
|---|---|
| evidence excerpt | **carries the secret** — it is the raw page text, by design |
| chain log payload | does not carry excerpts at all, so nothing reaches the trace |
| knowledge pipeline | refuses the sentence (`contains_secret`) |
| memory write policy | rejects — `openai-key`, `url-credentials`, `aws-secret-key` |
| outbound answer redaction | replaces all three with `[REDACTED:<kind>]` |
| live source registry | **0 scanner hits across 5 151 rows** |

The excerpt holding the secret is not a defect: it is the fetched page, and the
verifier must see what the page actually said. What matters is that every path
OUT of it is closed, and each was checked separately.

**Yesterday's fix propagated without being wired.** The `url-credentials` and
`aws-secret-key` patterns added for H-15 are now enforced at the memory
boundary and in the outbound redaction, because both delegate to the scanner
rather than keeping their own list. That is the difference between a fix and a
patch: one place learned, three boundaries got stricter.

**A distinction kept:** the stores THEMSELVES accept a secret when written
directly — `EpisodicMemoryStore.save` and `PersistentMemoryStore.save` have no
opinion about content. The protection is the policy in front of them, not the
store. Recorded so nobody later «simplifies» by writing straight to a store.


### H-18 — two bounds, and only one of them is the real one

Atlassian's incident was not a backup failure; it was a throughput mismatch —
damage arrives at once, repair proceeds one item at a time. Locally the same
shape would be the producer filling a queue only a human empties: 48 ticks a
day for seven days is 336 chances to propose.

Two mechanisms bound it, and the probe separated them because they answer
different questions:

* the **dedup key** collapses proposals that are the SAME — 336 additions with
  one key leave a single 612-byte row. Without a key: 336 rows, 336 pending;
* the **in-flight gate** (`_unresolved_task`) refuses while any Stage-A item is
  unresolved, counting `approved`-but-unexecuted as unresolved.

The second is the real bound. The dedup key would do nothing against 336
*different* proposals, and an agent looking for work every half hour would
certainly find different things to propose. Measured behaviourally rather than
read: the gate holds on `pending`, holds on `approved`-but-unexecuted, and
releases on both `denied` and `executed` — the release matters as much, since a
gate that never opens after one refusal would stop the producer for the whole
week.


### H-20 — the two drifts are not one thing, and only one of them was ours

The paper's distinction earns its keep here, because the two halves gave
opposite answers.

**Commitment holds.** A checkpoint parked with a goal, then read back through a
freshly constructed store object, returns the goal byte-for-byte, with its kind
and status. The obligation survives the interruption because it lives in a file
rather than in a process — which is precisely what their ablation removed to
send goal-abandonment from 0.00 to 1.00.

**Binding did not.** `_candidate_engineering_task` classifies a goal as
engineering partly BECAUSE `_PY_TARGET_RE` finds a `.py` filename in it — and
then throws the match away. The decision object had no field for a target, so
the filename survived only inside `evidence`, as a verbatim echo of the goal
text. A consumer could read the sentence; it could not read the file.

That is binding drift exactly as defined, on the axis their ablation held at
zero. Fixed by making the decision carry what its own reason named:
`_named_target()` returns the match instead of a yes/no, and `target_path`
holds it. `None` means «the reason named no object», not «the object is
unknown» — the H-11 lesson about defaults that assert a past they never had.

The banked xfail is converted into an enforced test, and strengthened past what
it asked: it now checks the FIELD exists **and** that the value is in it, since
an empty field is the same loss with a column added. It also checks the reverse
— a goal naming no file must leave `target_path` at `None`, because inventing a
target would be a fabricated link rather than a preserved one.

**The other half stays banked, and honestly so.** `test_some_machine_road_hands_the_producer_a_target`
still xfails: no machine caller passes a target to the producer, so the producer
re-selects its own backlog candidate. This repair gives the decision something
to hand over; it does not build the road. Those are different claims and the
ledger keeps them apart.


### H-21 — the matrix found five more than the first case did

The first probe asked one question («can a done task be marked failed?») and
got «yes». Taking the whole matrix instead turned one finding into six: every
terminal state could become any other, and only `mark_running` was protected —
by the claim check, which exists for a different reason entirely.

`failed → done` is the one that matters. A row whose work failed becomes a
success after the fact, and everything counting by status — the cycle report,
useful-cycles, the capability bench — counts the rewrite rather than the run.
That is the «falsely report success» bullet, reachable through the store rather
than through any reasoning error.

**Reachability, stated narrowly.** In the tick there is one settle per task
(`apply_run_outcome`), so a double-settle needs an exception AFTER a successful
settle, with the outer handler settling again. Narrow — but the store offers
the move to every caller, including future ones, and the refusal belongs where
the transition lives rather than in each caller's discipline.

Terminal outcomes are now write-once: repeating the SAME outcome stays
idempotent (callers rely on it), `blocked` and `paused` stay rewritable because
neither is terminal — one waits on a human, the other on a clock.

**One weakness in this entry's evidence, stated rather than hidden.** The
mutation probe for this guard was not run — the operator interrupted it and
asked to move on. So the guard is supported by six red-then-green cases and the
full battery at 8740, but NOT by a demonstration that removing it reddens them.
Every other fix in this ledger carries that demonstration; this one does not
yet.


### H-25 — a clean measurement and a live mechanism, kept apart

The data is clean: 12 472 timestamps across every store, all timezone-aware,
not one naive. So this class is **not reproduced in the data** and the entry
says so.

The mechanism was live anyway. `datetime.fromisoformat("2026-08-24T10:00:00")`
returns a naive value, and `.astimezone(utc)` then treats it as LOCAL time — on
this machine 10:00 becomes 07:00Z, a silent three-hour shift. Against a
30-minute orphan timeout that means a live task reads as orphaned the moment it
is written, which re-opens the double-execution class MIR-033 measured closed.

Of seven parse sites that did not normalise, five compare against an aware
`now` and would raise `TypeError` — loud, and acceptable. Two shifted silently:
`task_queue` and `scheduler`. Only those two were changed.

**Why UTC rather than refusal.** Every writer in this repository emits UTC, so
a naive value can only come from a hand edit or a future writer, and both mean
UTC. Refusing to read such a file would stop the queue entirely over one edit;
reading it as UTC turns a silent error into no error. An explicit non-UTC
offset is still honoured — pinned, so the fix cannot become «everything is
UTC».


### H-26 — the two boundaries differ by CAPABILITY, not by strictness

One text, «My password is hunter2», is refused by the persistent-memory policy
and kept verbatim by the episodic store. That looks like an inconsistency to be
equalised, and equalising it would be the wrong move.

The keyword class **finds no span**. It says «this text is about a password»,
not «the password runs from character 12 to 19». A redactor needs a span; there
is nothing to cut, so it cannot act on the keyword class even in principle. A
boundary that decides about the WHOLE record — write it or refuse it — can use
the class, and does. The asymmetry follows from what each boundary is able to
do.

**No gate was added to the episodic path**, and the reason is measured rather
than argued: across 6 426 live rows (episodes, persistent memory, source
registry, write journal) there are **zero** hits of either class. Rejecting
episodes on a keyword would discard the record of any run that discussed
password handling — a false-rejection cost paid against a measured frequency of
zero.

What changed is that the difference is now **declared**: its root is pinned
separately from its effect, the pattern class is pinned as caught by BOTH
boundaries (so the tolerance stays narrow), and the zero measurement is re-taken
by the test rather than remembered. If the frequency ever stops being zero, the
decision not to build a gate has to be made again — and the test says so in its
own failure message.

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

### H-27 — почему первая починка этого же места закрыла половину

Гипотеза шла на класс «прерывание между внешним эффектом и его учётом». Она
воспроизвелась: между `assert_can_start` и `record` переживает только счёт
вызовов, а токены и стоимость — нет. Но чинить это нечем дёшево. Учёт сегодня
**точный, по данным провайдера** (984 успешных строки из 984 с `estimated=False`),
а преполётная оценка сама объявлена «нарочно грубой, не биллинговой». Резерв
оценки заменил бы точность грубостью; поправочная запись потребовала бы
разрешить УМЕНЬШЕНИЕ счётчика, то есть снять монотонность — само свойство
безопасности. Реальная цена дыры: медиана вызова 12 единиц, пять убийств
процесса за неделю — 60 из 15000, то есть 0,4 %. Машинерия не заслужена.

Зато сверка, построенная ради этой гипотезы, нашла другое и живое. Резервов
`llm_calls` — 1364, строк расхода — 1379. Разрыв НЕ объясняется историей: строк
старше первого резерва ноль. Он весь в openai (659 против 644) и весь в двух
днях — 19 августа (+13) и 20-го (+2). Переключение провайдера ни при чём:
отказов anthropic в те дни ноль, а 14-17 августа, при 391 отказе, счета сошлись
точь-в-точь. Те же 15 строк отсутствуют и среди `model_cost_units`.

Причина: `agent_tick` строил леджер для выбора цели от хартии своей строкой,
мимо `app/bootstrap.py`. И резерв, и запись стоимости стоят под
`if self.budget_ledger is not None`, поэтому вызов оставлял строку расхода и
оставался невидим для суточного и недельного потолка. Заодно конструктор вместо
`from_env` обнулял и сессионные лимиты.

Отдельный урок в том, ПОЧЕМУ это пережило починку. Комментарий на месте правки
называет вскрытие 19 августа: тогда нашли вызовы вовсе без леджера и подключили
леджер расхода. Мерили видимость в учёте — её и починили. Вторую половину,
видимость для потолка, никто не мерил, и она осталась. Класс «покрытие списали
у соседа»: у первого варианта полное, у следующего дыра ровно в той детали,
которую не измеряли.

Побочный вывод о методе: обе текстовые пробы, закреплявшие прежнюю починку,
покраснели от ПЕРЕЕЗДА построения, а не от потери свойства. Проба, которую
ломает переезд, меряет адрес, а не свойство; обе переписаны на инвариант.

### H-28 — три сенсора, и последний упирается в границу, поставленную нарочно

Класс дублирующейся оплаченной работы проверялся тремя способами, потому что
первый сенсор был косвенным.

**Первый — кластеры одинакового входа.** 86 повторов, 8,7 % всех успешных
вызовов, кластерами до восьми. Само по себе это ничего не доказывает:
одинаковое ЧИСЛО токенов входа не есть одинаковый запрос.

**Второй — разрывы внутри кластеров.** Медиана 70 с, теснее 10 с всего две
пары. Это ритм, а не петля повторов. Гипотеза «повтор после отказа» проверена
отдельно: событий `replan_attempt` в 128 файлах трасс — **десять**, а не 86.
Первое объяснение умерло.

**Третий — деньги по исходам кампании.** Холостых 30, повторных 39,
заблокированных 82 — у всех ноль трат, и это правда, а не пустое поле:
`repeat` и `idle` решаются правилами (сборщик сигналов читает файлы состояния и
зовёт `select_best_next_action`, без модели), а `blocked` приходит через
ИЗМЕРЕННУЮ ветку, где трата считается дельтой до и после. Контроль, без
которого зелёный результат ничего бы не значил: тем же сенсором `completed`
показывает 386 вызовов и 2871 единицу. Сенсор умеет видеть положительное.

**Что осталось неизвестным, и почему это не дыра в аудите.** Доказать, что
восемь вызовов подряд несли ОДИН И ТОТ ЖЕ запрос, нельзя: запросы не хранятся.
Это не упущение, а граница, поставленная нарочно — сохранённый запрос нёс бы
секреты, и весь слой редакции существует ради обратного. Класс поэтому не
«защищён» и не «воспроизведён», а **неаудируем изнутри по решению, которое
дороже самого класса**. Названо здесь, чтобы следующий проход не считал
молчание защитой.

### H-29 — половина урока была соблюдена, и именно вторая половина стоит денег

Порчу репозиторий ЗАМЕЧАЕТ: строка с несошедшимся хешем уезжает в
`.quarantine`, файл переписывается без неё, улика цела. Это первая половина
урока ZFS, и она сделана хорошо. Вторая половина — не отдать молча МЕНЬШЕ
данных — не сделана: `read_state_jsonl` возвращает список короче, и вызывающий
ничем не отличает его от полного.

Для большинства хранилищ это терпимо: потеряна одна запись памяти. Асимметрия в
том, что у СЧЁТЧИКА-ПОТОЛКА потеря строки работает в сторону РАЗРЕШЕНИЯ. Замер:
суточный потолок в три вызова исчерпан, четвёртый отвергнут; порча одной строки
— и четвёртый разрешён. Ни предупреждения, ни события, а читателя у
`.quarantine` нет нигде в репозитории.

Проба доказана до того, как ей поверили. Первый вариант «порчи» добавлял пробел
и хеш не ломал: вызов оставался отвергнут, и это читалось бы как «защита
держит». Настоящая подмена значения дала обратный ответ. Зелёный результат
непроверенной пробы здесь был бы прямо противоположен истине.

Живых случаев нет: карантин пуст по всем хранилищам за четыре недели. Поэтому
РЕШЕНИЕ не менялось — «отказывать при нечитаемых книгах» останавливает агента
на неделю и стоит денег, а деньги принадлежат оператору. Закрыто ровно
молчание, и закрыто на поверхности, которую оператор и так читает
(`--status`), а не ещё одним журналом без читателя — этот класс уже
зарегистрирован как MIR-138 и повторять его было бы странно.

Свидетель самой опасности оставлен зелёным нарочно: он фиксирует ФАКТ, что у
потолка потеря строки открывает трату. Если поведение станет fail-closed, тест
обязан покраснеть и потребовать переписать эту запись, а не подгонки.

**Решение оператора 2026-08-24, на прямой вопрос с тремя вариантами:** оставить
поведение как есть, закрыть только молчание. Отвергнуты fail-closed (одна
порченая строка остановила бы работу на всю безнадзорную неделю) и досчёт
потерянных строк по максимальной виденной цене (оценка поверх испорченных
данных). Записано здесь, а не только в чате, потому что через месяц вопрос
«кто это выбрал» должен иметь ответ в репозитории.

### H-31 — растяжка вместо машинерии, потому что дефекта ещё нет

Внутри одного процесса подмены кода нет: `importlib.reload` не встречается
нигде, модули загружены один раз. Но процессов бывает два — плановый демон и
запущенный руками тик, — а лента самоприменения правит тот же репозиторий.
Общее у них состояние на диске, и там нашлось неприятное.

Строка с чужим форматом не игнорируется. Она признаётся повреждённой, уезжает в
карантин, и живой файл ПЕРЕПИСЫВАЕТСЯ без неё. То есть процесс на старом коде,
один раз прочитав хранилище, стирает из живого файла всё, что записал процесс на
новом. Это ровно форма Knight Capital: убыток делает не рассинхрон, а то, что
старая половина действует.

Дефекта сегодня нет: формат один, версии два не существует, случаев ноль.
Поэтому здесь не строится ни совместимость, ни переговоры о версиях — оба
решения оплачивались бы сейчас за опасность, которой пока нет. Вместо этого
поставлена растяжка на константу формата, и она несёт доказательство внутри:
второй тест показывает само стирание. Проверено ломкой — поднятие константы до
`v2` краснит оба теста.

Чего растяжка НЕ делает: она не запрещает менять формат. Она говорит тому, кто
меняет (включая самого агента через self-apply), что смена обязана приехать
вместе с читателем, принимающим обе версии.

### H-32 — защищено не тем, чем кажется, и остаток назван

Оживление припаркованных строк ограничено партией в три, и урок EBS записан в
самом коде: «сток тика выполняет ВСЁ, что pending, за один проход, поэтому
неограниченное оживление потратило бы обновлённое окно на долг одним залпом».
Это уже понято и уже сделано.

Но граница стоит на оживлении, а не на стоке. Сам сток — `for task in
pending_tasks` без предела, и времени тик не считает вовсе: ни бюджета секунд,
ни проверки срока. Держат его только денежные потолки.

Поэтому вопрос был не «ограничен ли сток», а «есть ли чем его затопить», и он
решался перечислением, а не допущением. Производителей строк ровно два:
добавление руками через CLI и планировщик расписаний, который заводит РОВНО ОДНУ
задачу на сработавшее расписание. Расписаний в живом хранилище ноль. Плюс партия
оживления в три. Затопить нечем.

**Остаток, названный явно.** Условие защиты — малое число расписаний, а не
свойство стока. Если когда-нибудь появится флот расписаний, неограниченный сток
станет достижим, и тогда всплывёт вторая половина: у тика нет предела по
времени, а внешний ограничитель времени выполнения убьёт процесс посреди
работы — то есть класс H-27(a), прерванный платный вызов. Здесь ничего не
строится: условие ещё не наступило. Записано, чтобы наступление заметили.

### H-33 — из двух ошибок настройки молча проходила та, что снимает потолок

Живой замер: `config/budget_limits.json` существует, а лимитов в окружении нет
ни одного. Значит весь денежный потолок агента держится на ОДНОМ файле. Убрать
его — и окна остаются с нулевыми пределами, а нулевой предел читается кодом как
«ограничения нет», а не как «тратить нельзя»: 500 резервов подряд, молча.
Тот же файл с испорченным содержимым при этом бросает `ValueError`. Одно место,
два противоположных ответа, и тихо проходит ровно опасный.

**Место починки искалось трижды, и оба промаха были содержательны.**

Первая попытка запрещала расхождение «назван, но отсутствует» в самом
`budget_ledger` — покраснело 168 тестов. Посылка была неверна: путь к конфигу
называют ВСЕ вызывающие по умолчанию, поэтому «назван» там не значит «оператор
его завёл».

Вторая ставила проверку в `run_tick` — покраснело 17 тестов, гоняющих тик на
временных папках. Попытка сузить условием «есть ключ» не помогла: ключ живёт в
оболочке разработчика, и тесты его наследуют.

Третья нашла настоящую границу — ВХОД ПРОЦЕССА. Плановый безнадзорный тик
приходит только через `python agent_tick.py`; тесты зовут `run_tick` внутри
себя. Разделяет то, что разделяет реальность, а не то, что удобно назвать.

**Проводка доказана процессом, а не вызовом.** Первая ломка не покраснела:
тесты звали функцию напрямую, и снятие её с пути ничего не меняло. Это ровно
случай «зелёные тесты ≠ подключено». Тест переписан на запуск модуля
подпроцессом и проверяет не только код возврата, но и ПРИЧИНУ отказа — иначе он
закрепил бы любое чужое падение.

### H-34 — одно решение оспорено замером, другое подтверждено тем же замером

Проба нашла три протекающие формы: КЛЮЧ словаря, множество и байты. Разница
между ними и есть содержание записи.

**Ключи трогать не стал.** В докстринге `redact_payload` записано решение:
ключи описывают схему, переписать их — потерять журнал. Спорить следовало не с
решением, а с его посылкой, и посылка проверяема: 739 различных ключей в 6489
живых строках — все до одного имена полей или переменных окружения, ни одного
«данными». Посылка держится, решение остаётся, а тест на границу закрепляет
именно её: если ключи однажды начнут редактироваться, это должно быть отдельным
осознанным решением с новым замером, а не побочным следствием правки обхода.

**Множества и байты закрыты.** Для них решения не было — обход просто в них не
заходил, а логгер их принимает, приводя к строке. §7 при этом объявляет
инвариант АБСОЛЮТНЫМ: три поверхности никогда не получают сырых секретов. Он
был ложен, и починка стоит нескольких строк, ничем не оплаченных.

Две частности, за которые пришлось отвечать. Множество после редакции
возвращается СПИСКОМ: два разных секрета дают одну метку, и множество молча
потеряло бы элемент — в журнале лучше две одинаковые метки, чем недосчёт.
Байты становятся строкой намеренно: значение, которое нельзя показать
безопасно, стоит меньше, чем показанное безопасно.

**Проба искала по файлу, а не по коду.** Прочие тесты сериализуют payload так
же, как логгер, и потому доказывают редактор, но не проводку. Отдельный тест
пишет настоящий журнал и читает настоящий файл — дефект был найден именно так,
и закрепляется там же. Ломка сделана по каждой ветке отдельно: обезвреживание
любой из двух краснит ровно свою форму.
