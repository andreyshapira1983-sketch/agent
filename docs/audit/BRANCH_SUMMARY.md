# What this branch contains

Published as `work/2026-08-27-autonomy-hardening`; the same commits are also the
local working branch `self-apply/20260815T191927Z`, which is where they were
made. Measured against `main` on 2026-08-27.

**279 commits at the moment this document was written**, including the commit
that added it. The count moves as the branch does — read it as a measurement with
a date on it, not as a property of the branch. The first version of this file
said 278 and named only the local branch: written five minutes before the push,
it described a different object than the one a reader would be standing on, which
is the exact defect shape this branch spent a week removing from other people's
captions.

By kind: 112 docs, 73 fix, 29 test, 21 feat, 20 audit, 7 chore,
5 refactor, 4 verify. The docs share is not padding — in this repository a
finding is not considered closed until its measurement, its rejected
alternatives and its boundary are written down where the next reader will hit
them.

**What grew.** The defect registry went from 99 entries to 173, every one
carrying its own measurement and provenance. The test suite went from 529 files
to 664, and the full battery stands at 9023 passing with 13 deliberate xfails.
Lint debt is held at exactly 113 by a ratchet that forbids growth.

---

## The method, because it explains the shape of everything below

Nothing here was fixed on the strength of an argument. Each entry required a
**red witness** — a test that fails for the stated reason before the change —
and, where practical, a **break test**: disable the fix and confirm exactly the
intended test reddens while the controls stay green. Where a claim could not be
measured it was recorded as unproven rather than asserted, and several entries
exist precisely to say «this was suspected and disproved».

Three habits did most of the work, and they are worth naming because they
generalise:

* **Prove the probe.** A zero means nothing without a positive control. Several
  findings began as «nobody reads this field» and turned out to be a bad query.
* **Ignorance is not a verdict.** Again and again the same defect shape appeared:
  a value meaning «not measured» treated as if it meant «measured and failed», or
  «not recorded» treated as «there were no grounds». Half a dozen fixes on this
  branch are that single distinction, in different organs.
* **A guard must bite.** Several pins turned out to protect a location — a
  function name, a line number, a string in the source — rather than a property.
  Three of them reddened on refactors during the final day and were rewritten to
  follow the meaning.

---

## The main threads

### Memory, and what it is allowed to learn from
The gate that decides which episodes may steer later answers was **selecting
inversely to the verifier signal**: of 65 verified records 9 were admitted, of 79
unverified ones 65 were. Honest partial work with real evidence was thrown away
while a machine-minted `lesson` tag walked in. Retrieval had the same shape — the
tag bought a fixed boost larger than any relevance score, so a record sharing two
words with the question outranked one sharing forty-one.

Fixed by separating rights that one token had been buying at once: surviving the
store, going first in retrieval, informing the planner, and being replayed
verbatim are four different permissions and now answer to different questions.

### Authority, and who granted it
The runtime could READ a standing grant and nothing shipped could CREATE one —
the mechanism for unattended operation existed in code and was reachable by no
operator action. Related work made every registry entry name its provenance, gave
the operator a goal veto, and made a cycle record remember whether the goal
actually drove the choice (measured: it did in 14% of 267 cycles).

### The self-apply lane
A proposal carries a whole file and used to write it back without asking whether
it was still the same file, so an approval given late could silently overwrite
newer work. The lane now stamps the pre-image at proposal time and refuses on a
mismatch. Documents were also taught to survive the pruner: a procedure's only
record of what it was for lived inside the text retrieval scores against, which
made it useless as evidence.

### Adversarial defence
The injection guard was found to **block the agent from its own defect registry**
— 263 findings in the file, because that file is where injection samples are
quoted. The fix keeps the verdict and changes the consequence for material
committed to this repository, deciding «ours» by provenance rather than location,
because location as a trust signal had already been disproved here in August.

The same day showed the guard writing excerpts of untrusted text into a journal
whose reader was exempt from scanning, and that the exemption's premise — «these
tools return framework-shaped text» — was false for all three tools it covered.

### The prospective audit
A separate pass asked, for each of the 47 historical failure classes already
collected, whether an unattended agent built from this repository could develop
the same mechanism as its capabilities expand. All 47 now carry a verdict. The
majority are disproved or protected, each naming what does the protecting.

Its central result is a single sentence: **the agent's autonomous effect surface
is creating new files inside the workspace, and nothing else** — no overwrite, no
self-modification, no push, no concurrent agents, no shell beyond an allowlist.
Most historical classes are unreachable because the hand is small, not because
the guards are clever, which is why every capability added moves several classes
at once. The audit records, for each protection, the condition that would end it.

### Closing the loop
Measured against history rather than today's tree: of 27 lane proposals, 11
touched only files that did not exist when they were made, and all 11 were
documents. That whole class waited for a human to open an inbox. It now applies
itself through the lane — which adds targeted tests, the full battery and
automatic rollback to an effect the agent could already perform bare — under two
gates and with the permission recorded in the rule's name rather than as
«unattributed».

---

## State at the tip

Battery 9023 passed / 13 xfailed, lint held at 113, both size ratchets and the
function-length ratchet clean, the anatomy map and the CNS census in sync with
the code they describe.

Three entries are `code_fixed_needs_runtime_verification`: their evidence is due
from the next unattended tick's journal, and the exact event to look for is
written into each. Nothing about them is claimed until it appears.
