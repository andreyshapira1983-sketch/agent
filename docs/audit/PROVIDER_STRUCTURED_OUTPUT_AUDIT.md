# Provider-layer audit — is strict JSON Schema reachable from here?

Step 1 of the structured-outputs capability spike (operator instruction,
2026-07-20). **Read-only.** Nothing in the provider layer, the completion
marker path, the persistent schema, eligibility, credit/debit, parser policy
or the MIR-057 migration was changed. No live API call was made and no live
store was touched.

The question this document answers is narrow and deliberately so: *can a
strict JSON Schema declaration reach the models we actually route to, and what
would break on the way?* It does **not** argue that structured outputs are the
right mechanism. Per the operator's framing, Structured Outputs are a
candidate replacement for the **transport channel of the declaration** — they
remove a class of syntactic schema non-compliance — and are **not** a
replacement for the structural overrides (`assemble_completion_state`) or for
verification. A schema can guarantee that `completion` is one of N strings. It
cannot guarantee the string is the right one.

---

## Method

Static reading of the provider layer on branch `mir-057-task-completion-axis`,
plus two in-memory probes of `core.verifier` (§8) that allocate no files, open
no sockets and read no store. Files read: `core/llm.py`, `core/model_router.py`,
`core/model_catalog.py`, `core/model_discovery.py`, `config/model_catalog.json`,
`config/model_registry.json`, `core/loop.py` (synthesis path),
`core/completion_marker.py`, `core/smart_memory.py`, `core/verifier_core.py`,
`core/verifier_utils.py`, `core/verifier_patterns.py`, `.env` (key **presence
and length only** — no secret value was read or recorded).

---

## 1. The adapter has no structured-output surface at all

**`core/llm.py` is the only place in the repository that talks to a model
provider.** Four call sites, all of them in that one file:

| line | call | surface |
|---|---|---|
| `core/llm.py:317` | `client.messages.stream(...)` | Anthropic |
| `core/llm.py:405` | `client.messages.create(...)` | Anthropic |
| `core/llm.py:359` | `client.chat.completions.create(...)` | OpenAI, streaming |
| `core/llm.py:453`, `:459` | `client.chat.completions.create(...)` | OpenAI, non-streaming |

Across the whole repository there are **zero** occurrences of
`response_format`, `json_schema`, or `strict`. The OpenAI-compatible path
passes exactly `model`, `messages`, `max_tokens` / `max_completion_tokens`,
`temperature` (non-o-series only), and — when streaming — `stream` and
`stream_options`. There is no `**kwargs` passthrough.

So the first-layer answer is unambiguous and is not model-specific:

> **Today, strict JSON Schema is unreachable for every model, because the
> adapter cannot express it.** Whatever the providers support, the request we
> send cannot ask for it.

Two consequences for the spike. First, "does terra support strict" is not the
blocking question — the adapter is. Second, any structured path is necessarily
**new adapter code**, so it can be added beside the existing method rather
than inside it, which is what keeps the marker path byte-identical.

**Surface note.** We are on **Chat Completions**, not the Responses API. The
two configure structured output differently and differ in how refusal and
incomplete states are reported. A spike must pick one deliberately; adopting
the Responses API would be a second change of transport riding along with the
first, and should not be bundled.

## 2. Provider-native tool calling does not exist in this agent

There is no `tools=` or `tool_choice=` argument at any provider call site. The
planner executes tools itself and the results are injected into the
synthesizer's **user prompt** as `<evidence>` text blocks
(`core/loop.py:3800-3819`, via `_format_artifact` and `apply_total_budget`).

This changes what scenario 2b ("response after tools") means. At the provider
level there is no post-tool-call turn to test — every synthesis is a
single-turn completion. What actually varies after tools is **prompt size and
shape**. So the spike must not test "structured output after tool calls"; it
must test **schema adherence under a large injected evidence payload**, which
is a different experiment with a different failure mode.

## 3. Which models are actually on the live paths

Resolved by executing the router's own resolution functions (no network):

| tier | provider preference (`_TIER_PROVIDER_PREF`) | resolved model |
|---|---|---|
| light | `huggingface` → `openai` | **`openai / gpt-5.4-nano-2026-03-17`** |
| standard | `openai` | **`openai / gpt-5.6-terra`** |
| deep | `anthropic` | `anthropic / claude-opus-4-8` |

> **⚠ CORRECTION (2026-07-21, measured against a real session start).** The
> table above is the **per-question** routing (`ModelRouter.for_task`) and is
> confirmed correct. It was incomplete in a way that matters: **three models sit
> on live paths, not two.**
>
> The operator's session banner prints `llm_model=gpt-5.4-mini` with
> `reason=policy:balanced:…` — that is the **role default** (`for_role`),
> resolved from the custom entries in `config/model_registry.json`
> (`my-current-planner`, `my-cheap-summary`), not from the tier catalog.
> Reproduced by building the router exactly as `app/bootstrap.py:143` does
> (`ModelRouter.from_env()`), which returns `gpt-5.4-mini` for the planner role
> while `for_task` returns nano for a trivial question and terra for an ordinary
> one.
>
> **Consequence for §7:** the capability probe must cover **`gpt-5.4-mini`** as
> well. By the registry it serves `verifier` and `memory_summary` — so the
> verifier, the component MIR-060 is about, runs on a model this audit did not
> list. Paths calling `for_role` rather than `for_task`
> (`core/loop_methods.py:247`, `:499`, `main.py:1066`, and the bootstrap
> defaults) all land on it.
>
> Nothing else in this section is revised; the tier resolution stands as
> measured.

Three things worth recording:

- **`huggingface` is preferred for the light tier but never wins.**
  `config/model_catalog.json` contains only `anthropic` and `openai` provider
  sections, so `tier_model_for(LIGHT, "huggingface")` is empty and the router
  skips it with `no_model:huggingface` even though `HF_TOKEN` is set. Nano is
  reached by fallback, not by preference. That is a fragile route: adding a
  huggingface section to the catalog would silently move the light tier off
  nano.
- **The exact nano id is dated — `gpt-5.4-nano-2026-03-17`, not `gpt-5.4-nano`.**
  A capability probe must use the id the router actually emits.
- **The deep tier is Anthropic, not OpenAI.** `o4-mini-2025-04-16` is
  configured under `openai.tier_best.deep` but is only reachable if the
  Anthropic credential disappears. Structured output on the deep tier is an
  Anthropic question and is out of this spike's scope.

Nano's live role is the **cheap path** (`core/loop.py:1560-1563`, synthesizer
role with `force_tier=ComplexityTier.LIGHT`) — i.e. nano can and does write
final synthesis output. That is exactly the class the operator flagged: output
that feeds durable memory and procedural feedback.

## 4. Model capability: undetermined, and not determinable from this repo

`config/model_catalog.json` stores `{id, tier}` per model and nothing else.
`core/model_discovery.py` builds that catalog from the provider's model
listing and records **no capability flags** — no structured-output field, no
schema-support field, no probe result. Nothing anywhere in the repository, in
`docs/`, or in `data/` records a structured-output attempt against any model.

**I will not assert from memory whether `gpt-5.6-terra` or
`gpt-5.4-nano-2026-03-17` support strict JSON Schema.** Both post-date my
knowledge cutoff, the model-name families are not ones I can reason about by
analogy, and a confident guess here is precisely the failure this project has
repeatedly registered against itself.

**This question is answerable only by a live probe**, which means real
requests and real spend against `OPENAI_API_KEY`, and is therefore an operator
decision rather than something to fold silently into a read-only audit. The
minimal probe is specified in §7.

## 5. The six scenarios, as they stand today

### 5a. Ordinary response
Single-turn `chat.completions.create`, `temperature=0.5`, no `response_format`
(`core/loop.py:3948`). A structured path adds the parameter; nothing else about
this scenario is unusual.

### 5b. Response after tools
Not a provider-level concept here — see §2. Test instead: schema adherence with
a full `<evidence>` payload at the evidence budget ceiling.

### 5c. Streaming — a live production path, and the costliest interaction
`stream_complete` is called at `core/loop.py:3944` whenever `_stream_on_token`
is set (set for the run at `:590`, cleared for retries at `:1584` and for the
replay/refusal branches). This is not a debug path; it is how the user sees an
answer arrive.

Two distinct problems, both structural:

1. **The stream carries the transport, not the answer.** Under a JSON schema
   the token stream is JSON syntax. Either the user watches raw JSON scroll
   past, or the adapter must incrementally extract the `answer` field from a
   partial JSON document. The second is real work and is the single largest
   hidden cost in this spike. It must be measured, not assumed.
2. **`stream_complete` has no truncation handling whatsoever.** Unlike
   `complete()`, it never inspects a finish reason and never continues. Today a
   truncated stream yields short prose; under a schema it yields **invalid
   JSON** — a hard failure where there is currently a soft one.

### 5d. Refusal — a gap that fails *silently*, and is introduced by the change
`_complete_openai_compatible` returns `(choice.message.content or "")`
(`core/llm.py:471`). Under structured outputs a model refusal populates
`message.refusal` and leaves `content` null, so the adapter would return `""`,
`complete()` would return `""`, and **the synthesizer would produce an empty
answer with no error raised anywhere**.

Note the direction of this: today, without structured outputs, a refusal is
ordinary prose that the marker path can label `refused`. Adopting the
transport would convert a *labelled refusal* into a *silent empty answer*
unless the adapter reads `refusal` explicitly. Nothing downstream is prepared
for it either — every existing use of the word "refusal" in this codebase
(`core/operational_domain.py:64`, `core/loop.py:2681,2703,2742`) means the
**agent's own** policy refusal, never the provider's field.

### 5e. Max-token / incomplete — structurally incompatible with the current continuation design
`complete()` auto-continues when the stop reason is `max_tokens` / `length`,
up to `AGENT_MAX_CONTINUATIONS` (default 4), by **string-concatenating legs**
(`core/llm.py:225-247`). On the OpenAI path a continuation replays the partial
answer as an assistant message plus a "continue exactly where you stopped"
user message (`core/llm.py:446-450`).

Under a strict schema this does not degrade — it breaks:

- a truncated leg is **invalid JSON**, so there is nothing to parse;
- each continuation leg is itself schema-constrained, so the model must emit a
  **complete new JSON object**, not a suffix;
- concatenating two JSON objects produces a string that is neither.

So auto-continuation and strict structured output cannot both be on for the
same call as currently written. This forces a real design choice, listed in §9.

### 5f. Long production system prompt
`SYSTEM_ANSWER` measures **6,624 characters**; `marker_instruction` adds **945**;
`LOCAL_CRITIQUE_SYSTEM_ADDENDUM` and a `prompt_registry` override
(`core/loop.py:3744-3748`) can add more. Roughly 1.9k tokens before any evidence.

Length alone is unremarkable. The interaction is not. `SYSTEM_ANSWER` mandates
an Output Contract (Conclusion / Facts headers) and a citation grammar, and
`output_contract_requires_headers()` (`core/loop.py:3905`) makes the **verifier**
expect those headers — `malformed_output` is computed from their presence
(`core/verifier_core.py:231`). A structured `answer: string` must therefore
carry the full contract-formatted prose *inside* a JSON string value. The spike
must measure whether schema-constrained generation degrades **contract
adherence and citation grammar**, because that prose is the verifier's input.
A schema that produces valid JSON containing an answer the verifier then calls
malformed has moved the failure, not removed it.

## 6. The spike schema — and a correction to its enum

The instruction specified `completion: enum` over "the current seven states".
**The current declaration vocabulary is five, not seven**, and the difference
is load-bearing rather than cosmetic:

- `CompletionState` (`core/smart_memory.py:33-41`) has seven members:
  `achieved, partially_achieved, blocked, refused, failed, cancelled, unknown`.
- `CompletionDeclaration` (`:46-48`) has **five** — `cancelled` and `unknown`
  are excluded *on purpose*, documented in place as "facts about the run's
  termination that the loop observes, never something the answer gets to
  claim". `marker_instruction` is invoked with `_COMPLETION_DECLARATIONS`
  (`core/loop.py:3753`), and `assemble_completion_state` reaches `cancelled`
  and `unknown` only through structural facts the caller supplies.

Putting seven values in the spike schema would let the model declare
`cancelled` — widening the declaration channel is a **semantic policy change
smuggled in as a transport change**, which is the one thing this spike was
scoped to avoid. Recommended spike schema, unchanged in meaning from the
marker path:

```json
{
  "name": "agent_completion_spike",
  "strict": true,
  "schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["answer", "completion"],
    "properties": {
      "answer": { "type": "string" },
      "completion": {
        "type": "string",
        "enum": ["achieved", "partially_achieved", "blocked", "refused", "failed"]
      }
    }
  }
}
```

If the operator does intend to widen the vocabulary to seven, that is a
separate decision about who may declare what, and it should be taken on its
own evidence — not inherited from a schema draft.

## 7. What the capability probe must establish

Six requests per model — **`gpt-5.6-terra`, `gpt-5.4-nano-2026-03-17`, and
`gpt-5.4-mini`** (the third added 2026-07-21, see the correction in §3; it is
the role default and the registry's `verifier` model) — against the schema in
§6, on Chat Completions, recording the raw response envelope:

1. **strict accepted at all** — does the request return 200 or a 400 naming
   `response_format` / `json_schema`?
2. **ordinary response** — valid JSON, `completion` inside the enum.
3. **large evidence payload** — same, at the evidence-budget ceiling (§5b).
4. **truncation** — force a low `max_completion_tokens`; record the exact
   `finish_reason` and whether `content` is partial JSON, null, or absent.
5. **refusal** — a prompt the model should decline; record whether `refusal`
   is populated and what `content` is.
6. **streaming** — whether `stream=True` composes with the schema, and what the
   final chunk carries.

Plus one control: the same six against the **existing marker path**, so the
comparison in Step 2 has a baseline measured under identical conditions rather
than one recalled from earlier runs.

**This probe has not been run.** It spends real API budget and makes external
requests; it is proposed here, not performed.

## 8. Registered separately: derived-claim verification (MIR-060)

Recorded at operator instruction as a distinct defect, because **Structured
Outputs cannot fix it** — it is a property of what the verifier checks, not of
how the answer is transported. Full entry with measured evidence:
`docs/audit/MASTER_ISSUE_REGISTRY.md` § MIR-060.

Short form: once a citation resolves to an evidence record, no content check of
the claim against the excerpt ever runs, except a narrow literal figure
containment test gated on `_STAT_TRIGGER_RE`. A false computed claim carrying a
resolvable citation is marked `verified`.

## 9. Open decisions — operator's, not the spike's

1. **Run the live capability probe?** Real spend, external requests, ~12
   requests plus controls (§7). Blocks everything downstream.
2. **Truncation policy on a structured path** (§5e). Either disable
   auto-continuation for structured calls and treat truncation as a hard
   failure, or **split the channels** — prose from the ordinary call, the
   declaration from a separate small structured call, which sidesteps
   truncation, streaming and refusal all at once at the cost of one extra
   request per synthesis. The second option deserves weighing first; it is the
   only one that does not require the streaming JSON parser of §5c.
3. **Enum width — five or seven** (§6).
4. **Chat Completions or Responses API** (§1).

## Constraints carried into this document

- **The `−73%` (Voyager) and `−28–35% ECE` figures are not used here and must
  not appear in any architectural justification** until the exact primary
  table, the metric definition, and its applicability to our *categorical*
  completion model have been shown. No claim in this document rests on them.
- Adversarial framing is **not** part of this spike. It is a separate
  experiment to be run after the structured-output result is in, so the causal
  attribution of either effect is not lost.
- A separate critic is **not** designed here. Its input contract — task, trace,
  tool results, exit codes, evidence, environment state — must be designed
  first; the final answer text alone is insufficient input.

---

*Provenance: static reading of the provider layer plus two in-memory verifier
probes, on `mir-057-task-completion-axis`. No live model call, no store access,
no code changed. Where this document and the code disagree, the code wins and
this file must be corrected.*

---

# ADDENDUM (2026-07-21) — architectural placement of §1–§9's conclusions

> **Process note.** §1–§9 above were written **before** the target-architecture
> layer was read. This is a **labeled, dated addendum** that corrects *where the
> conclusions belong*, per the doc-state discipline that a document is annotated
> rather than silently rewritten.
>
> **No measured fact in §1–§9 is revised.** The adapter facts, the resolved
> routes, the six scenarios, the prompt sizes and the five-token declaration
> vocabulary all stand exactly as measured. What changes is placement and
> vocabulary, not evidence.
>
> **Documents read before writing this addendum** (operator-set gate):
> `README.md`, `AGENT_DOCTRINE.md`, `архитектура автономного Агента.txt`,
> `docs/future/CORPORATE_MODEL.md`, `docs/CENTRAL_AGENT_GOVERNANCE.md`,
> `docs/ROADMAP.md`, `docs/daemon-progress.md`, `docs/AGENT_ANATOMY.md` — all in
> full. For the MIR-060 wording change additionally: `docs/audit/MEMORY_MAP.md`,
> `docs/MEMORY_SYSTEM_AUDIT.md`, `docs/self-audit-lessons.md`, and the committed
> MIR-060 entry — in full; `docs/MEMORY_FIX_PLAN.md` and
> `docs/audit/MEMORY_LIFECYCLE_CONTRACT.md` §6/§8–§15 — by targeted section and
> keyword search rather than cover-to-cover, stated here so the limit is visible.
> **Not read, and therefore no claim about sub-agents is made anywhere in this
> addendum:** `docs/SUBAGENT_LIFECYCLE.md`,
> `docs/MULTI_AGENT_COORDINATION_LAYER.md`.

## A1. The target architecture already foresees this layer

The canonical target-architecture text names, as distinct components:

- **§5 Tools, Actions & Execution** — *Capability Discovery & Negotiation*;
  *Tool Schema Drift Detection*; *MCP / Tool Adapter Protocol*.
- **§7 Autonomy Governance** — *Capability Awareness*; *Limitation Awareness*;
  *Operational Design Domain* (with *Allowed Tool Categories*, *Out-of-Domain
  Detection*).
- **§9 Learning & Self-Improvement** — *Skill / Capability Library*.

So the question §1–§9 ran into is not unanticipated by the design. Per that
document's own reading rule, a named item means "foreseen by the target
architecture", **not** "implemented".

## A2. What was not found, stated with its scope

**In the provider/model path examined by this audit** — `core/llm.py`,
`core/model_router.py`, `core/model_catalog.py`, `core/model_discovery.py`,
`config/model_catalog.json`, `config/model_registry.json` — no explicit
implemented **capability registry**, **capability-probing contract**, or
**freshness / invalidation mechanism** was found.

This is a scope-bounded negative. It says nothing about paths this audit did not
examine, and it is **not** a claim that any architecture section is empty.

## A3. What the router does today, as observed

In this specific path, the router uses **model identity and tier as a partial
surrogate for capability information**: tier is assigned by name pattern
(`core/model_catalog.py:11-23, 61-82`), and role/tier selection consumes it
(`core/model_router.py:1154-1171`).

This is an **observed present state**. It is explicitly **not** a claim that §6
Model Management has architecturally displaced §5 Capability Discovery — no such
substitution has been established, and two nearby facts cut against reading one
into it: `docs/AGENT_ANATOMY.md` groups `model_catalog` / `model_discovery` /
`model_router` under its own **"Model Management (§6 / §12)"** heading, never
under its §5 group; and `core/capability_request` is grouped under **§7**, with
the §5 group holding effect gateways, receipts and compensation.

Separately observable: `docs/ROADMAP.md` (Tracks A–G plus "What is deliberately
NOT here yet") contains no track for capability discovery or negotiation. It is
neither marked IMPLEMENTED or PLANNED, nor listed as deliberately deferred.

## A4. Capability is at least three axes, not one

Any capability record this work eventually produces must keep these apart:

| axis | question | example of where it already lives |
|---|---|---|
| **authority** | is the use *permitted*? | Policy Gate, governance modes, `core/capability_request`, `core/deep_escalation` (deep/Opus is reason-gated) |
| **availability** | is the function *technically supported* by this route? | **no home found in the examined path** |
| **competence** | does it work *reliably enough for this task*? | not addressed by this audit |

The deep-escalation gate is the clearest illustration of the split: the system
governs carefully whether it *may* open a deep model, and has nothing that
records whether a given model *can* do a given thing.

## A5. Permission denial is not unsupported capability

These six states are distinct and must not be collapsed into one value:

`denied_by_policy` · `unsupported` · `unprobed` · `unavailable` · `stale` ·
`probe_failed`

Collapsing them would make an authority decision indistinguishable from a
technical fact, and a never-measured route indistinguishable from a measured
negative. The §5 companion component *Tool Schema Drift Detection* is the
architecture's own signal that `stale` has to be representable.

## A6. What a live probe would and would not produce

A live Structured Outputs probe, **if ever authorised**, would produce the first
**observable capability records for specific model / provider / adapter
combinations** — a record keyed on the triple, not on a model name, and carrying
its measurement date and method.

It would **not** constitute the Capability Discovery architecture. One measured
capability over two routes is an input to that design, not the design.

**The probe has not been run.** The capability of `gpt-5.6-terra` and
`gpt-5.4-nano-2026-03-17` remains **undetermined**, and no answer to it may be
inferred, recalled, or guessed (§4 above).

## A7. §6's enum decision, restated in the architecture's vocabulary

§6 above recommended the **five** `CompletionDeclaration` tokens rather than the
seven `CompletionState` values. The architecture's §3 *Task Acceptance &
Definition of Done* separates *User-Defined Acceptance Criteria* and
*System-Defined Completion Rules* / *Done-Not-Done Classifier* — the same
boundary the code draws between what the answer may declare and what the loop
observes. The recommendation is unchanged; it now has a named home.

*(Placement only. This makes no status claim about MIR-057, whose registry entry
is untouched.)*

## A8. MIR-060 — architectural references added, defect unchanged

MIR-060 remains a **reproducible present defect**, measured and re-measurable;
its registry entry is the status owner and its description is not replaced by any
"unbuilt slot" framing. Architectural references have been **added** there:
§5 *Tool Result Validation → Semantic Validation*, and §8 *Evidence Consistency
Check* / *Factuality Evaluation*.

One finding from the reading gate belongs here because it affects planning: the
M1 memory-lifecycle contract does **not** close MIR-060. Its verification rules
(§7.4, §12, §14 of `docs/audit/MEMORY_LIFECYCLE_CONTRACT.md`) are entirely
**trust-class** rules — a resolved citation counts `verified` unless the matched
evidence's `trust_class` is `agent_derived` or `replay_echo`. A `file`-kind
evidence derives `trust_class = tool_observation`, which is in the allowed set,
so every case measured in MIR-060 would still count `verified` under that
contract as drafted. This is not a fault in the contract — its §1 scopes it to
record trust — but it must be recorded so no one plans on the assumption that M1
covers the content axis.

## A9. What this addendum does not change

- Every measured fact in §1–§9.
- The four open decisions in §9 — still open, still the operator's.
- MIR-060's status, description, and closure criteria.
- MIR-057 and MIR-058, whose entries are untouched.
- The standing constraints: the Voyager `−73%` and `−28–35% ECE` figures remain
  barred from architectural justification pending their primary table, metric
  definition and applicability to a categorical completion model; adversarial
  framing stays a separate later experiment; the critic is not designed until its
  input contract is.

*Addendum provenance: reading of the source-of-truth document set listed in the
process note, against code already measured in §1–§9. No live model call, no
store access, no code changed by this addendum.*
