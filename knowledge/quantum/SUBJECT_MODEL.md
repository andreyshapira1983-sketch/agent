# What a `.qm` artifact is about — working note, Step 1

**Not a schema. Not adopted.** This records a candidate subject model, the evidence
that forced it, and the attacks it has and has not survived. Two candidates are
already dead and are kept here as evidence, per the lab's rule that failed
representations are results.

Status at the bottom: **Step 1 is NOT complete.** One attack is unresolved.

## Why the question exists

`main.qm` is named after `main.py`. Of its 81 non-prose facts, **two** are about
`main.py`'s bytes (`subject.path`, `subject.sha256_prefix`). The rest describe
outcome states, an observer, and the internal structure of a different file in a
different language. The name and the content are about different things.

## Candidate 0 — file-centric: `filename ↔ semantic subject`

**FALSIFIED** before this step, by probe QT1: editing `cli/app.py` made a claim in
`main.qm` false while `main.py` stayed byte-identical (`def397b4c33e51d3` before
and after) and all eight cited tests stayed green. A subject whose truth is
controlled by files it does not name is not that file.

## Candidate 1 — the single junction: one carrier + the roles bound to it

Proposed in this step: the subject is one **carrier** (a channel holding
distinguishable states) crossing one **execution boundary**, with producer,
consumers, observers, consequences, validity and unproven scope attached as roles.

Motivation was real. Classifying all 81 facts against it left only 12 unslotted,
and the entry-point experiment had behaved exactly as it predicts: `main.py` is
neither carrier nor producer, and removing it destroyed no state and no observer —
all four baseline behaviours reproduced through `cli.app.run_cli`.

**FALSIFIED, in this step, by measurement.** Two runs were held equal on the
exit-code carrier and compared elsewhere:

| run | exit code | first line of stdout |
| --- | --- | --- |
| `python main.py --help` | 0 | `usage: main.py [-h] …` |
| `argv[0]='main.qm'`, `run_cli()` | 0 | `usage: main.qm [-h] …` |

One class on the exit-code carrier — indistinguishable to *every* observer of that
carrier, including `selfcheck.ps1`. Two classes on the stdout carrier: 2 of 33
lines differ. A subject with one carrier cannot hold the result of the very
experiment that motivated it.

## Candidate 2 — current, under attack

The evidence forces at least this much structure. Stated as constraints, not as
fields:

| # | constraint | forced by |
| --- | --- | --- |
| E1 | the subject is not a file | QT1; 2 of 81 facts concern the named file |
| E2 | the invoked file is a **transit**, not a role — replaceable without semantic loss | entry experiment: `main.py` absent, all four behaviours reproduced |
| E3 | a subject spans **several carriers**, each with its own state space | the `--help` probe above |
| E4 | facts about **instantiation** fit no carrier or observer slot | the 12-fact residue: call-site count, `argv0`, forbidden path |
| E5 | one consumer binds **several carriers**, and several **surfaces** per carrier | `selfcheck.ps1:23–29` — `Tee-Object` takes merged stdout/stderr to a log and a console, `$LASTEXITCODE` feeds both `$ok` and printed text |
| E6 | the producer of record is a **symbol plus a termination contract**, not a file | the launch worked from `cli.app.run_cli` with `main.py` gone |

So the unit is: **one named execution boundary; the carriers that cross it; per
carrier a state space, a producer, consumers, and observation surfaces with
domains; plus the binding that instantiates the whole thing — including transits,
which are named but hold no semantics.**

### The law this model makes falsifiable

> Replacing a transit preserves every carrier's state space and every observer's
> partition.

The entry experiment is its first test. It held for the exit-code carrier, the
stderr text of the preflight refusal (byte-identical after CRLF normalisation),
and the `SystemExit` transit. It **failed in exactly one place**: `argv[0]` is
consumed by argparse as `prog` and surfaces on the stdout carrier. The exception
is localised, measured, and predicted-against — which is what makes the law worth
keeping rather than weakening.

## Attacks

**A1 — is "boundary" just the file under another name?**
Named without reference to `main.py`: *one process invocation whose producer is
`cli.app.run_cli`*. The entry experiment proves this identifier is the load-bearing
one — the boundary survived the file's deletion. **SURVIVES**, with a recorded
consequence: boundary identity is parasitic on producer identity, so validity must
bind the producer's source. The current artifact binds `main.py` and
`scripts/selfcheck.ps1` and **does not bind `cli/app.py`** — which is precisely the
file QT1 used to falsify it. A demonstrated gap, not a hypothetical one.

**A2 — is the carrier set closed?** **UNRESOLVED — this is what blocks Step 1.**
The boundary emits at least: exit code, stdout, stderr, log files, memory writes,
and network calls to the model. If the subject is "all carriers", it can never be
completed, and certifying it is meaningless. A consumer-relative closure ("all
carriers with a named consumer") matches the lab's existing INSTANTIATED /
HYPOTHETICAL discipline, but nothing yet proves that closure is well-founded — a
new consumer can appear at any time and silently widen the subject. No probe has
been run against this.

**A3 — does the residue really need its own part, or is it a producer fact?**
P4 asserts six call sites, four passing `main.py`. That is neither producer nor
consumer alone; it is the consumer's invocation of the producer, and it names a
**transit** — an object E2 says carries no semantics but which must still be
nameable. **SURVIVES**, and it is what promoted "transit" from a description to a
part of the model.

**A4 — does the model earn its keep on the fact the old one could not express?**
The unexpressible fact was one consumer occupying two observer classes. Under
candidate 2 it is ordinary: one consumer, one carrier, two surfaces — plus a
second carrier the old model had no room for at all. **SURVIVES.**

## Status

Candidate 2 survives A1, A3, A4 and is blocked on A2. **Step 1 is not complete and
Step 2 must not begin.** What must be settled first: whether the carrier set of a
boundary can be closed by a rule that is itself checkable, or whether a `.qm`
subject is inherently open and must say so in a way a validator can act on.

Nothing here is adopted, and no field names are proposed. `main.qm` is unchanged.
