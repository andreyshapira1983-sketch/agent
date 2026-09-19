"""Never ask a model to reproduce a file it was not shown (MIR-065).

`RepairProposalGenerator` truncated the target file to `max_context_chars` and
then asked, in `_SYSTEM_PROMPT`, for ``proposed_content`` = "complete
replacement content for the target file". Above the window the request is
impossible by construction: the model is asked to reproduce bytes it never saw.

Measured on the real repository before the fix — 43 of 145 `core/` modules
exceeded the 16 000-char window, and the request was issued anyway:

    loop.py         196 916 chars — model shown   8%
    planner.py      117 338 chars — model shown  13%
    smart_memory.py  64 526 chars — model shown  24%

Two live attempts on `core/loop_methods2.py` (36 959 chars, 43% shown):

* `gpt-4o-mini` complied and returned a stub — the diff read as "536 lines
  changed", i.e. a proposal to delete the 57% it had not been shown. Rejected by
  the 200-line cap, which is the only reason it did not reach a human.
* `claude-opus-4-8` returned 543 tokens and no content — it declined to invent
  the missing part. Rejected as "proposed_content must be a non-empty string".

The stronger model looked like the bigger failure. That is the tell: the defect
was in the request, not the answer.

`core/self_build_producer.py` already solved this, and its comments say why
(`_BUILDER_MAX_TOKENS`, `_MAX_SPLIT_TARGET_LINES`): a single-shot builder cannot
safely rewrite a very large module, so an oversized target is "refused up-front
instead of burning a doomed generation". These tests hold the repair path to the
same rule.
"""
from __future__ import annotations

from pathlib import Path

from core.repair_proposal import (
    DEFAULT_MAX_CONTEXT_CHARS,
    RepairProposalGenerator,
)


class _ExplodingLLM:
    """Any call is a failure: an oversized target must not reach a model."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        raise AssertionError(
            "the model was called for a target that does not fit the context "
            "window — this is the doomed generation the fix exists to prevent"
        )


class _RecordingLLM:
    """Captures the prompt so a test can check the file arrived whole."""

    def __init__(self, reply: str = '{"diagnosis": "d", "target_file": "x.py", '
                                   '"proposed_content": "print(1)\\n", '
                                   '"evidence": ["t"], "confidence": 0.9}') -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **kwargs):
        self.prompts.append(user)
        return self.reply


def _generator(llm, workspace: Path, **kwargs) -> RepairProposalGenerator:
    return RepairProposalGenerator(llm=llm, workspace_root=workspace, **kwargs)


def _write(workspace: Path, name: str, size: int) -> str:
    """A syntactically real module of roughly `size` characters."""
    line = "x = 1  # padding to reach the requested size\n"
    body = line * (size // len(line) + 1)
    (workspace / name).write_text(body[:size], encoding="utf-8")
    return name


# ── 1. refuse up front, do not call the model ────────────────────────────────

def test_an_oversized_target_is_refused_without_calling_the_model(tmp_path: Path):
    llm = _ExplodingLLM()
    target = _write(tmp_path, "huge.py", DEFAULT_MAX_CONTEXT_CHARS + 5_000)

    report = _generator(llm, tmp_path).generate(target_path=target)

    assert llm.calls == 0, "an impossible request must not be paid for"
    assert report.status in {"rejected", "tool_error"}, report.status
    assert any("too large" in w.lower() or "context" in w.lower()
               for w in report.warnings), report.warnings


def test_the_refusal_says_the_size_and_the_limit(tmp_path: Path):
    """The operator has to be able to act on it, so both numbers appear."""
    llm = _ExplodingLLM()
    size = DEFAULT_MAX_CONTEXT_CHARS + 1_234
    target = _write(tmp_path, "huge.py", size)

    report = _generator(llm, tmp_path).generate(target_path=target)

    blob = " ".join(report.warnings)
    assert str(size) in blob, blob
    assert str(DEFAULT_MAX_CONTEXT_CHARS) in blob, blob


# ── 2. within the window, the file arrives whole ─────────────────────────────

def test_a_file_inside_the_window_is_sent_untruncated(tmp_path: Path):
    llm = _RecordingLLM()
    target = _write(tmp_path, "small.py", 2_000)

    _generator(llm, tmp_path).generate(target_path=target)

    assert llm.prompts, "a target inside the window must reach the model"
    assert "[truncated]" not in llm.prompts[0], (
        "the model was shown a truncated file and will still be asked for the "
        "complete replacement content"
    )


def test_no_prompt_ever_carries_a_truncation_marker(tmp_path: Path):
    """The invariant behind both halves: shown-in-full, or not asked at all."""
    llm = _RecordingLLM()
    exploding = _ExplodingLLM()

    _generator(llm, tmp_path).generate(
        target_path=_write(tmp_path, "ok.py", DEFAULT_MAX_CONTEXT_CHARS - 100)
    )
    _generator(exploding, tmp_path).generate(
        target_path=_write(tmp_path, "over.py", DEFAULT_MAX_CONTEXT_CHARS + 100)
    )

    assert exploding.calls == 0
    for prompt in llm.prompts:
        assert "[truncated]" not in prompt


# ── 3. a reply that thinks out loud before the JSON is still usable ──────────
#
# Measured on `claude-opus-4-8`, twice, after the window fix let it see the whole
# file: 41 244 characters of reply, opening with *"I'll analyze the failing tests
# to understand what's e…"*. The JSON was in there; the parser only looked at
# character 0 and reported "invalid JSON: Expecting value: line 1 column 1".
#
# The prompt does say "Return ONLY valid JSON, no markdown". Restating it harder
# is not a fix — a reasoning model narrating before it answers is normal, and a
# mechanism that only works when the model is terse is a mechanism that fails on
# the strongest model available. Two paid calls were spent on this exact reply.


def test_json_is_found_after_a_prose_preamble():
    from core.repair_proposal import _parse_json_object

    raw = (
        "I'll analyze the failing tests to understand what's expected.\n\n"
        "The block swallows every exception, so a TypeError reads as an outage.\n\n"
        '{"diagnosis": "one except for two causes", "target_file": "core/x.py", '
        '"proposed_content": "print(1)\\n", "evidence": ["test_x"], '
        '"confidence": 0.8}'
    )
    parsed = _parse_json_object(raw)

    assert parsed["ok"], parsed.get("error")
    assert parsed["data"]["confidence"] == 0.8
    assert parsed["data"]["target_file"] == "core/x.py"


def test_json_is_found_when_prose_follows_it_too():
    from core.repair_proposal import _parse_json_object

    raw = (
        "Here is the proposal:\n"
        '{"diagnosis": "d", "target_file": "core/x.py", '
        '"proposed_content": "x = 1\\n", "evidence": [], "confidence": 0.5}\n'
        "\nLet me know if you want a narrower change."
    )
    parsed = _parse_json_object(raw)

    assert parsed["ok"], parsed.get("error")
    assert parsed["data"]["diagnosis"] == "d"


def test_braces_inside_strings_do_not_end_the_object():
    """The extraction must balance braces, not stop at the first `}`."""
    from core.repair_proposal import _parse_json_object

    raw = (
        "Reasoning first.\n"
        '{"diagnosis": "handles a dict literal {\\"a\\": 1} in the code", '
        '"target_file": "core/x.py", '
        '"proposed_content": "d = {\\"k\\": {\\"n\\": 1}}\\n", '
        '"evidence": [], "confidence": 0.4}'
    )
    parsed = _parse_json_object(raw)

    assert parsed["ok"], parsed.get("error")
    assert "{\"k\"" in parsed["data"]["proposed_content"]


def test_a_reply_with_no_json_at_all_still_reports_cleanly():
    from core.repair_proposal import _parse_json_object

    parsed = _parse_json_object("I cannot safely propose a change to this file.")

    assert not parsed["ok"]
    assert "invalid JSON" in parsed["error"]


def test_an_unusable_reply_is_diagnosable_from_the_journal():
    """The failure must say what came back, or the next step is a paid guess."""
    from core.repair_proposal import ProposalGenerationReport

    report = ProposalGenerationReport(
        status="llm_error",
        warnings=["LLM returned invalid JSON: Expecting value: line 1 column 1"],
        raw_response="I'll analyze the failing tests first, then propose a change.",
    )
    head = report.summary()["raw_response_head"]

    assert head is not None
    assert head["starts_with_brace"] is False
    assert "analyze" in head["head"]
    assert head["length"] > 0


# ── 3b. the holes review found in the first version of this fix ──────────────
#
# All three were real, and two of them defeated the fix in exactly the case it
# was built for. Kept as their own tests so the next change cannot quietly undo
# them.


def test_a_brace_in_the_preamble_does_not_hide_the_real_object():
    """The reasoning itself may mention braces — that must not shadow the JSON.

    The first version locked onto `text.find("{")`. A model narrating "the code
    builds {'k': 1} before writing" produces a balanced span that parses as
    nothing, and the real object further down was never tried. That is the
    narrating-model case this whole fix exists for.
    """
    from core.repair_proposal import _parse_json_object

    raw = (
        "I'll analyze this. The block currently builds {not: json} before the "
        "write, and a second literal {a, b} appears in the comment.\n\n"
        '{"diagnosis": "d", "target_file": "core/x.py", '
        '"proposed_content": "x = 1\\n", "evidence": [], "confidence": 0.6}'
    )
    parsed = _parse_json_object(raw)

    assert parsed["ok"], parsed.get("error")
    assert parsed["data"]["confidence"] == 0.6


def test_a_json_looking_but_wrong_object_does_not_win_over_the_real_one():
    """A parseable non-answer earlier in the reply must not be accepted."""
    from core.repair_proposal import _parse_json_object

    raw = (
        'Example of the shape I will return: {"note": "illustration only"}\n\n'
        '{"diagnosis": "real", "target_file": "core/x.py", '
        '"proposed_content": "x = 1\\n", "evidence": [], "confidence": 0.9}'
    )
    parsed = _parse_json_object(raw)

    assert parsed["ok"], parsed.get("error")
    assert parsed["data"].get("diagnosis") == "real", (
        "an illustrative object earlier in the reply was accepted as the answer"
    )


def test_the_size_check_measures_what_is_actually_sent(tmp_path: Path):
    """Redaction can grow the text, and the prompt is built from the redacted copy.

    `[REDACTED:<kind>]` is not the same length as the secret it replaces. A file
    that fits before redaction can exceed the window after it, and then
    `_build_prompt` truncates — reintroducing "reproduce what you were not
    shown" through a different door.
    """
    from core.redaction import redact_text

    # Not every secret grows under redaction: an `sk-…` key shrinks (49 -> 27
    # chars), an AWS-shaped id grows (21 -> 26). Only the growing kind produces
    # this failure, so the fixture uses that one — and asserts the premise below
    # rather than trusting it. The first version of this test used a shrinking
    # secret and therefore passed while testing nothing.
    #
    # Assembled from fragments so a static credential scanner does not read the
    # fixture as a real key; `redact_text` sees the joined string at runtime,
    # which is the only thing this test depends on.
    aws_shaped = "AKIA" + "IOSFODNN7" + "EXAMPLE"
    secret_line = f"sample_id = [{aws_shaped}]\n"
    body = secret_line * (DEFAULT_MAX_CONTEXT_CHARS // len(secret_line) - 20)
    (tmp_path / "secrets.py").write_text(body, encoding="utf-8")

    redacted, _ = redact_text(body)
    assert len(body) <= DEFAULT_MAX_CONTEXT_CHARS, "fixture must fit before redaction"
    assert len(redacted) > DEFAULT_MAX_CONTEXT_CHARS, (
        "fixture does not exercise the case: redaction did not grow it past the "
        f"window ({len(body)} -> {len(redacted)})"
    )

    llm = _ExplodingLLM()
    report = _generator(llm, tmp_path).generate(target_path="secrets.py")

    assert llm.calls == 0, "redaction-expanded content reached the model unchecked"
    assert report.status in {"rejected", "tool_error"}, report.status
    assert any("redaction grew it" in w for w in report.warnings), report.warnings


def test_a_refusal_before_the_call_is_not_reported_as_an_empty_reply(tmp_path: Path):
    """"Never asked" and "answered with nothing" need to stay distinguishable.

    That distinction is the entire reason `raw_response_head` exists; the
    oversized-target refusal was rendering as `{"length": 0, "note": "empty
    reply"}`, which is what a silent model looks like.
    """
    llm = _ExplodingLLM()
    target = _write(tmp_path, "huge.py", DEFAULT_MAX_CONTEXT_CHARS + 2_000)

    head = _generator(llm, tmp_path).generate(target_path=target).summary()["raw_response_head"]

    assert llm.calls == 0
    assert head is None or "not called" in str(head).lower(), (
        f"a pre-call refusal renders as a model reply: {head}"
    )


def test_the_output_budget_is_configurable_like_the_window(tmp_path: Path):
    """Both budgets belong to the instance, or only one of them is tunable."""
    llm = _RecordingLLM()
    generator = _generator(llm, tmp_path, max_output_tokens=1234)

    assert generator.max_output_tokens == 1234


# ── 4. the window must actually cover the codebase ───────────────────────────

def test_the_window_matches_the_self_build_builder(tmp_path: Path):
    """One codebase, one answer to 'how big a file may a single shot rewrite'.

    `self_build_producer` sized its own limit at 60 000 bytes with a 16 000-token
    builder. A repair window far below that means the two self-modification
    paths disagree about the same question for no stated reason.
    """
    from core.self_build_producer import _MAX_CONTENT_BYTES

    assert DEFAULT_MAX_CONTEXT_CHARS >= _MAX_CONTENT_BYTES, (
        f"repair sees {DEFAULT_MAX_CONTEXT_CHARS} chars but self-build allows "
        f"{_MAX_CONTENT_BYTES}; the smaller one silently decides what the agent "
        f"can repair"
    )


def test_the_window_covers_most_of_core(tmp_path: Path):
    """A repair mechanism blind to a third of the package is not a mechanism.

    Not a snapshot of a count — a floor. Before the fix 43 of 145 modules were
    truncated (70% covered); the window must keep ordinary modules reachable so
    that a refusal means "this file is genuinely huge", not "the limit is low".
    """
    core = Path(__file__).resolve().parent.parent / "core"
    modules = list(core.glob("*.py"))
    visible = [p for p in modules if p.stat().st_size <= DEFAULT_MAX_CONTEXT_CHARS]

    covered = len(visible) * 100 // len(modules)
    assert covered >= 95, (
        f"only {covered}% of core/ fits the repair window "
        f"({len(modules) - len(visible)} modules refused up front)"
    )
