# Repository Guidelines

## Safety

- Work only with existing files in the current working tree.
- Never inspect `.git`, Git history, or the contents of deleted files.
- `git status --short` is allowed.
- Do not run `git log`, `git show`, `git reflog`, or an unscoped `git diff`.
- Never restore deleted files.
- Never run commit, push, reset, clean, checkout, restore, rebase, or merge without explicit permission.
- Preserve all existing user changes.
- Do not modify files outside this repository.

## Scope boundary for Quantum work

The subject of a Quantum unit is always an **agent-side** decision relation,
state, carrier, consumer, or consequence. The external LLM is a black-box
resource and an observable consequence boundary — "a model call occurred" versus
"it did not" — never the organism being dissected.

- Do NOT decompose an LLM, provider SDK, model weights, or transformer behavior.
  Finding aliens inside GPT is out of scope.
- Do NOT open `model_router`, provider selection, or model capabilities unless
  the current MEASURED causal chain independently forces that boundary to become
  the next subject. (Precedent: C2's causal closure was forced into
  `_llm_factory` by tracing a journal value — that is the only licensed way in.)
- Do NOT treat conceptual diagrams as architecture specifications. A node missing
  from the code is evidence about the diagram, not an invitation to build it.
- The operating pattern is fixed:
  `existing state/value → carrier → consumer → decision → downstream path →
  observable consequence`, then
  `prove reachability → prove preconditions → mutate ONE relation → predict →
  RED → restore bytes → GREEN`.
- Observer boundaries are explicit. Two paths with `LLM calls = 0` may still be
  semantically different — agent built or not, `.env` read or not, durable
  mutation or not.
- HARD STOP: analyzing model internals, provider behavior beyond an agent-side
  causal edge, or designing a future resource-selection pipeline means the
  quantum boundary has been crossed. Stop and report the exact causal edge that
  tempted the expansion. One relation per unit; adjacent machinery is not
  chased because it is interesting.

## Physical construction rule for Quantum work

**One physical implementation file → exactly ONE Python function definition → one
semantic responsibility.** Literally one `def`, counted over the whole module,
nested definitions included — not "one behavioral callable plus helpers". The
looser wording let a 447-line file with eight private helpers describe itself as
one responsibility, which is how the debt below accumulated. This replaces the line
budget as the architectural boundary. The repository already showed why: a 400-line limit
produced small files that still shared hidden state and had no real module
boundary, so line count measured the wrong thing.

- A file holding one long function with several independent responsibilities does
  **not** satisfy the rule.
- If one function contains two independently observable decisions, carriers,
  consumers or consequences that can be separated **without inventing semantics**,
  they are separate operations and belong in separate files.
- Data-only definitions, constants, schemas and import/re-export modules are not
  behavioral implementation files. They are treated separately, and must never
  become containers for hidden executable logic.
- Every new behavioral file states its connections explicitly:
  `input/preconditions → one operation → output/carrier → named consumer`,
  and that connection must **bite under mutation** before the next operation is
  built.
- Build one operation, one file, prove it, connect it to the Quantum graph, then
  the next. Do not batch-create small files and connect them afterwards.
- `.qm` semantics bind to the resolved callable or semantic boundary, never to the
  filename. A filename is only a physical carrier.

**Mechanically enforced.** `tests/test_qm_one_operation_ratchet.py` counts the
definitions in every `scripts/qm_*.py`. A file already above one is DEBT with a
recorded ceiling that may only fall; a file not in that table is new and must define
exactly one. The ceiling is never raised: a new operation gets a new file.

**Legacy code is not refactored to satisfy this.** Old responsibilities move only
when the active Quantum construction path reaches them and the move can be proven
behaviorally equivalent. File-size guards may remain as secondary checks; they are
no longer the boundary.

## Development Workflow

- Before changing code, explain the plan and name the files you intend to modify.
- Make the smallest focused change that solves the task.
- Do not install dependencies or modify lock files without permission.
- Determine test commands only from existing current configuration files. Do not guess.
- Add or update a regression test when fixing a defect.
- Run focused tests first, followed by the complete test suite.
- Run configured formatting, linting, and type checks when available.
- Never claim completion when required tests are failing.
- If a test cannot run, report the exact command, error, and probable cause.
- Finish with a summary of changed files, commands executed, test results, and remaining risks.
