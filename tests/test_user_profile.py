"""Tests for core/user_profile.py (Layer 4 — User Profile / Mental Model).

Coverage targets
----------------
Unit:
  - UserProfile default values
  - update_profile: expertise detection (novice / intermediate / expert)
  - update_profile: verbosity detection (brief / normal / detailed / locked)
  - update_profile: language detection (ru / en)
  - update_profile: interests extraction
  - update_profile: interaction_count increment
  - update_profile: technical flag follows expertise
  - update_profile: pure function (no mutation)
  - profile_to_prompt_block: structure and content
  - UserProfileStore: load/save/load_or_default/update_from_interaction

Integration:
  - AgentLoop with user_profile_store emits user_profile_load/update events
  - Profile accumulates across multiple run() calls
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.user_profile import (
    UserProfile,
    UserProfileStore,
    profile_to_prompt_block,
    update_profile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh() -> UserProfile:
    return UserProfile()


# ---------------------------------------------------------------------------
# 1. Default values
# ---------------------------------------------------------------------------

class TestUserProfileDefaults:
    def test_default_expertise(self) -> None:
        p = _fresh()
        assert p.expertise == "intermediate"

    def test_default_verbosity(self) -> None:
        p = _fresh()
        assert p.verbosity == "normal"

    def test_default_language(self) -> None:
        p = _fresh()
        assert p.language == "ru"

    def test_default_interaction_count(self) -> None:
        p = _fresh()
        assert p.interaction_count == 0

    def test_default_interests_empty(self) -> None:
        p = _fresh()
        assert p.interests == []

    def test_default_not_locked(self) -> None:
        p = _fresh()
        assert p.verbosity_locked is False
        assert p.language_locked is False


# ---------------------------------------------------------------------------
# 2. Expertise detection — expert signals
# ---------------------------------------------------------------------------

class TestExpertSignals:
    @pytest.mark.parametrize("text", [
        "Как работает GIL в Python?",
        "Explain biased reference counting in CPython",
        "What does PEP 703 change in the memory model?",
        "покажи episodic memory для этого эпизода",
        "расскажи про architecture и knowledge pipeline",
        "Что такое free threading in Python 3.13?",
        "context window и token budget для LLM",
        "O(n log n) amortized complexity",
    ])
    def test_expert_signal_detected(self, text: str) -> None:
        base = _fresh()
        updated = update_profile(base, text)
        assert updated.expert_signals > 0, (
            f"Expected expert signals for: '{text}'"
        )

    def test_expertise_becomes_expert_after_many_expert_signals(self) -> None:
        p = _fresh()
        for _ in range(5):
            p = update_profile(p, "GIL PEP 703 episodic memory kernel architecture")
        assert p.expertise == "expert"

    def test_expert_implies_technical_true(self) -> None:
        p = _fresh()
        for _ in range(5):
            p = update_profile(p, "GIL PEP 703 biased reference counting atomics")
        assert p.expertise == "expert"
        assert p.technical is True


# ---------------------------------------------------------------------------
# 3. Expertise detection — novice signals
# ---------------------------------------------------------------------------

class TestNoviceSignals:
    @pytest.mark.parametrize("text", [
        "что такое Docker?",
        "как работает интернет?",
        "что значит этот код",
        "explain simply how loops work",
        "what is a variable?",
        "for beginners: how does Python run code?",
    ])
    def test_novice_signal_detected(self, text: str) -> None:
        base = _fresh()
        updated = update_profile(base, text)
        assert updated.novice_signals > 0, (
            f"Expected novice signals for: '{text}'"
        )

    def test_expertise_becomes_novice_after_many_novice_signals(self) -> None:
        p = _fresh()
        for _ in range(8):
            p = update_profile(p, "что такое Docker? как работает? объясни просто")
        assert p.expertise == "novice"

    def test_novice_sets_technical_false(self) -> None:
        p = _fresh()
        for _ in range(8):
            p = update_profile(p, "что такое Docker? как работает? объясни просто")
        assert p.expertise == "novice"
        assert p.technical is False


# ---------------------------------------------------------------------------
# 4. Expertise stays intermediate without strong signals
# ---------------------------------------------------------------------------

class TestIntermediateExpertise:
    def test_neutral_question_stays_intermediate(self) -> None:
        p = _fresh()
        p = update_profile(p, "покажи список файлов")
        assert p.expertise == "intermediate"

    def test_mixed_signals_stay_intermediate(self) -> None:
        """Balanced expert/novice signals → intermediate."""
        p = _fresh()
        for _ in range(3):
            p = update_profile(p, "что такое GIL и как работает?")
        # "что такое" is novice, "GIL" is expert — should stay intermediate
        assert p.expertise == "intermediate"


# ---------------------------------------------------------------------------
# 5. Verbosity detection
# ---------------------------------------------------------------------------

class TestVerbosityDetection:
    @pytest.mark.parametrize("text", [
        "кратко расскажи",
        "вкратце что это?",
        "TL;DR",
        "give me a brief answer",
        "short answer please",
    ])
    def test_brief_detected(self, text: str) -> None:
        p = update_profile(_fresh(), text)
        assert p.verbosity == "brief"
        assert p.verbosity_locked is True

    @pytest.mark.parametrize("text", [
        "расскажи подробно",
        "объясни детально",
        "full explanation needed",
        "step by step please",
        "объясни подробнее",
    ])
    def test_detailed_detected(self, text: str) -> None:
        p = update_profile(_fresh(), text)
        assert p.verbosity == "detailed"
        assert p.verbosity_locked is True

    def test_neutral_keeps_normal(self) -> None:
        p = update_profile(_fresh(), "покажи логи")
        assert p.verbosity == "normal"

    def test_brief_can_be_overridden_to_detailed(self) -> None:
        p = update_profile(_fresh(), "кратко")
        assert p.verbosity == "brief"
        p = update_profile(p, "расскажи подробнее")
        assert p.verbosity == "detailed"

    def test_detailed_can_be_overridden_to_brief(self) -> None:
        p = update_profile(_fresh(), "подробно")
        assert p.verbosity == "detailed"
        p = update_profile(p, "кратко")
        assert p.verbosity == "brief"

    # ------------------------------------------------------------------
    # Regression: bare "подробнее" used as a contextual object modifier
    # (e.g. "покажи тесты подробнее") must NOT lock verbosity to detailed.
    # Only directive uses ("отвечай подробнее", "расскажи подробнее") lock.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("text", [
        "покажи только упавшие тесты подробнее",
        "выведи логи подробнее",
        "список задач подробнее",
    ])
    def test_contextual_подробнее_does_not_lock_verbosity(self, text: str) -> None:
        """'подробнее' modifying a specific object should NOT change the
        global verbosity preference."""
        p = update_profile(_fresh(), text)
        assert p.verbosity == "normal"
        assert p.verbosity_locked is False

    @pytest.mark.parametrize("text", [
        "отвечай подробнее",
        "говори подробнее",
        "пиши подробнее",
        "объясняй подробнее",
    ])
    def test_directive_подробнее_locks_verbosity(self, text: str) -> None:
        """Communication-verb + 'подробн*' is an explicit verbosity directive."""
        p = update_profile(_fresh(), text)
        assert p.verbosity == "detailed"
        assert p.verbosity_locked is True


# ---------------------------------------------------------------------------
# 6. Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_russian_detected(self) -> None:
        p = update_profile(_fresh(), "что такое агент и как он работает в системе")
        assert p.language == "ru"

    def test_english_detected(self) -> None:
        p = update_profile(_fresh(), "how does the agent work with the planning system")
        assert p.language == "en"

    def test_ambiguous_keeps_default(self) -> None:
        p = update_profile(_fresh(), "GIL")
        # Too short to detect language — keeps default "ru"
        assert p.language == "ru"

    def test_language_locks_after_3_interactions(self) -> None:
        p = _fresh()
        for _ in range(3):
            p = update_profile(p, "the agent plans and executes the task")
        assert p.language == "en"
        assert p.language_locked is True

    def test_locked_language_not_overridden_by_other_lang(self) -> None:
        p = _fresh()
        for _ in range(3):
            p = update_profile(p, "the agent runs the plan for the user")
        assert p.language_locked is True
        # Now send Russian — should not change language
        p = update_profile(p, "что такое агент и как он работает")
        assert p.language == "en"


# ---------------------------------------------------------------------------
# 7. Interests extraction
# ---------------------------------------------------------------------------

class TestInterestsExtraction:
    def test_architecture_interest_detected(self) -> None:
        p = update_profile(_fresh(), "расскажи про architecture агента")
        assert "agent-architecture" in p.interests

    def test_memory_interest_detected(self) -> None:
        p = update_profile(_fresh(), "как работает episodic memory?")
        assert "agent-memory" in p.interests

    def test_security_interest_detected(self) -> None:
        p = update_profile(_fresh(), "как работает защита от injection?")
        assert "security" in p.interests

    def test_testing_interest_detected(self) -> None:
        p = update_profile(_fresh(), "запусти pytest с coverage")
        assert "testing" in p.interests

    def test_multiple_interests_accumulated(self) -> None:
        p = _fresh()
        p = update_profile(p, "как работает episodic memory и architecture?")
        p = update_profile(p, "запусти pytest")
        assert "agent-memory" in p.interests
        assert "agent-architecture" in p.interests
        assert "testing" in p.interests

    def test_interests_are_unique(self) -> None:
        p = _fresh()
        for _ in range(5):
            p = update_profile(p, "episodic memory")
        assert p.interests.count("agent-memory") == 1

    def test_interests_capped_at_20(self) -> None:
        p = _fresh()
        for _ in range(30):
            # Generate unique pseudo-interests by including many keywords at once
            p = update_profile(
                p,
                "GIL episodic procedural memory architecture injection pytest "
                "asyncio coroutine multiagent knowledge"
            )
        assert len(p.interests) <= 20


# ---------------------------------------------------------------------------
# 8. Interaction count
# ---------------------------------------------------------------------------

class TestInteractionCount:
    def test_count_increments(self) -> None:
        p = _fresh()
        assert p.interaction_count == 0
        p = update_profile(p, "hello")
        assert p.interaction_count == 1
        p = update_profile(p, "world")
        assert p.interaction_count == 2

    def test_count_preserved_across_updates(self) -> None:
        p = _fresh()
        p = update_profile(p, "а")
        p = update_profile(p, "б")
        p = update_profile(p, "в")
        assert p.interaction_count == 3


# ---------------------------------------------------------------------------
# 9. Pure function — no mutation
# ---------------------------------------------------------------------------

class TestPureFunctionProperty:
    def test_original_not_mutated(self) -> None:
        original = _fresh()
        _ = update_profile(original, "GIL PEP 703 episodic architecture")
        assert original.interaction_count == 0
        assert original.expert_signals == 0
        assert original.expertise == "intermediate"

    def test_returns_new_object(self) -> None:
        p = _fresh()
        updated = update_profile(p, "GIL")
        assert updated is not p


# ---------------------------------------------------------------------------
# 10. profile_to_prompt_block
# ---------------------------------------------------------------------------

class TestProfileToPromptBlock:
    def test_block_contains_expertise(self) -> None:
        p = UserProfile(expertise="expert")
        block = profile_to_prompt_block(p)
        assert "expertise: expert" in block

    def test_block_contains_verbosity(self) -> None:
        p = UserProfile(verbosity="brief")
        block = profile_to_prompt_block(p)
        assert "verbosity: brief" in block

    def test_block_contains_language(self) -> None:
        p = UserProfile(language="en")
        block = profile_to_prompt_block(p)
        assert "language: en" in block

    def test_block_contains_interests(self) -> None:
        p = UserProfile(interests=["testing", "security"])
        block = profile_to_prompt_block(p)
        assert "testing" in block
        assert "security" in block

    def test_block_has_opening_tag(self) -> None:
        block = profile_to_prompt_block(_fresh())
        assert "<user_profile>" in block

    def test_block_has_closing_tag(self) -> None:
        block = profile_to_prompt_block(_fresh())
        assert "</user_profile>" in block

    def test_empty_interests_shows_general(self) -> None:
        p = UserProfile(interests=[])
        block = profile_to_prompt_block(p)
        assert "general" in block

    def test_technical_vocab_label(self) -> None:
        p_tech = UserProfile(technical=True)
        p_plain = UserProfile(technical=False)
        assert "technical" in profile_to_prompt_block(p_tech)
        assert "plain" in profile_to_prompt_block(p_plain)


# ---------------------------------------------------------------------------
# 11. UserProfileStore — persistence
# ---------------------------------------------------------------------------

class TestUserProfileStore:
    def test_load_returns_none_when_empty(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        assert store.load() is None

    def test_load_or_default_returns_default_when_empty(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        p = store.load_or_default()
        assert p.expertise == "intermediate"
        assert p.interaction_count == 0

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        p = UserProfile(expertise="expert", verbosity="brief", language="en")
        store.save(p)
        loaded = store.load()
        assert loaded is not None
        assert loaded.expertise == "expert"
        assert loaded.verbosity == "brief"
        assert loaded.language == "en"

    def test_load_returns_latest(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        p1 = UserProfile(expertise="novice")
        p2 = UserProfile(expertise="expert")
        store.save(p1)
        store.save(p2)
        loaded = store.load()
        assert loaded is not None
        assert loaded.expertise == "expert"

    def test_update_from_interaction_increments_count(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        p1 = store.update_from_interaction("hello world")
        p2 = store.update_from_interaction("second turn")
        assert p1.interaction_count == 1
        assert p2.interaction_count == 2

    def test_update_from_interaction_persists(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        store.update_from_interaction("GIL PEP 703 episodic architecture")
        # Reload from disk
        store2 = UserProfileStore(tmp_path / "profile.jsonl")
        loaded = store2.load()
        assert loaded is not None
        assert loaded.interaction_count == 1
        assert loaded.expert_signals > 0

    def test_update_from_interaction_with_base(self, tmp_path: Path) -> None:
        store = UserProfileStore(tmp_path / "profile.jsonl")
        base = UserProfile(interaction_count=10, expert_signals=5)
        updated = store.update_from_interaction(
            "architecture episodic memory",
            base=base,
        )
        assert updated.interaction_count == 11

    def test_file_created_on_save(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "profile.jsonl"
        store = UserProfileStore(path)
        store.save(UserProfile())
        assert path.exists()

    def test_corrupted_line_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.jsonl"
        # Write a valid record, then a corrupted line, then another valid record.
        p_good = UserProfile(expertise="expert")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(p_good.model_dump(mode="json")) + "\n")
            f.write("{INVALID_JSON\n")
            f.write(json.dumps(UserProfile(expertise="novice").model_dump(mode="json")) + "\n")
        store = UserProfileStore(path)
        loaded = store.load()
        # Most recent valid record should be "novice".
        assert loaded is not None
        assert loaded.expertise == "novice"


# ---------------------------------------------------------------------------
# 12. Integration: AgentLoop with user_profile_store
# ---------------------------------------------------------------------------

class TestAgentLoopUserProfileIntegration:
    """Verify that AgentLoop accepts user_profile_store and emits profile events."""

    def _make_loop(self, tmp_path: Path):
        """Build a minimal AgentLoop with a real UserProfileStore and a mock LLM."""
        from unittest.mock import MagicMock

        from core.loop import AgentLoop
        from core.logger import TraceLogger
        from core.policy import PolicyGate
        from tools.base import ToolRegistry

        registry = ToolRegistry()
        policy = PolicyGate(registry)
        logger = TraceLogger(trace_id="test-uprof", log_dir=tmp_path)

        mock_llm = MagicMock()
        mock_llm.provider = "mock"
        mock_llm.model = "mock-model"
        mock_llm.complete.return_value = (
            "Conclusion:\nOK\n\nFacts:\n- fact [general-knowledge]\n"
            "Sources:\n1. general-knowledge\nConfidence: low\n"
            "Unverified:\nnothing\nSafety:\nnothing"
        )

        profile_store = UserProfileStore(tmp_path / "profile.jsonl")

        loop = AgentLoop(
            registry=registry,
            policy=policy,
            llm=mock_llm,
            logger=logger,
            user_profile_store=profile_store,
            verifier_enabled=False,
            clarification_enabled=False,
        )
        return loop, profile_store

    def test_loop_accepts_user_profile_store(self, tmp_path: Path) -> None:
        loop, _ = self._make_loop(tmp_path)
        assert loop.user_profile_store is not None

    def test_profile_loaded_on_run(self, tmp_path: Path) -> None:
        loop, _ = self._make_loop(tmp_path)
        loop.run("hello world")
        assert loop.last_user_profile is not None

    def test_profile_updated_after_run(self, tmp_path: Path) -> None:
        loop, store = self._make_loop(tmp_path)
        loop.run("GIL PEP 703 architecture episodic memory")
        # Profile should have been saved to disk.
        loaded = store.load()
        assert loaded is not None
        assert loaded.interaction_count == 1

    def test_profile_accumulates_across_runs(self, tmp_path: Path) -> None:
        loop, store = self._make_loop(tmp_path)
        loop.run("hello")
        loop.run("world")
        loop.run("third")
        loaded = store.load()
        assert loaded is not None
        assert loaded.interaction_count == 3

    def test_profile_load_event_emitted(self, tmp_path: Path) -> None:
        loop, _ = self._make_loop(tmp_path)
        events: list[dict] = []
        loop.log._handlers = []  # type: ignore[attr-defined]

        # Spy: track log calls.
        original_log = loop.log.log

        logged: list[tuple] = []

        def spy(event: str, *args, **kwargs):
            logged.append((event, args, kwargs))
            return original_log(event, *args, **kwargs)

        loop.log.log = spy  # type: ignore[method-assign]
        loop.run("hello")

        event_names = [e for e, *_ in logged]
        assert "user_profile_load" in event_names

    def test_profile_update_event_emitted(self, tmp_path: Path) -> None:
        loop, _ = self._make_loop(tmp_path)
        original_log = loop.log.log
        logged: list[tuple] = []

        def spy(event: str, *args, **kwargs):
            logged.append((event, args, kwargs))
            return original_log(event, *args, **kwargs)

        loop.log.log = spy  # type: ignore[method-assign]
        loop.run("hello")

        event_names = [e for e, *_ in logged]
        assert "user_profile_update" in event_names

    def test_no_profile_store_does_not_crash(self, tmp_path: Path) -> None:
        """AgentLoop without user_profile_store should work as before."""
        from unittest.mock import MagicMock

        from core.loop import AgentLoop
        from core.logger import TraceLogger
        from core.policy import PolicyGate
        from tools.base import ToolRegistry

        registry = ToolRegistry()
        policy = PolicyGate(registry)
        logger = TraceLogger(trace_id="test-no-uprof", log_dir=tmp_path)

        mock_llm = MagicMock()
        mock_llm.provider = "mock"
        mock_llm.model = "mock-model"
        mock_llm.complete.return_value = (
            "Conclusion:\nOK\n\nFacts:\n- fact [general-knowledge]\n"
            "Sources:\n1. general-knowledge\nConfidence: low\n"
            "Unverified:\nnothing\nSafety:\nnothing"
        )

        loop = AgentLoop(
            registry=registry,
            policy=policy,
            llm=mock_llm,
            logger=logger,
            verifier_enabled=False,
            clarification_enabled=False,
        )
        result = loop.run("hello")
        assert isinstance(result, str)
        assert loop.last_user_profile is None


# ============================================================
# Security / validation tests (audit fixes)
# ============================================================

class TestUserProfileLanguageValidation:
    """Tests for the language field BCP-47 validator added in deep audit."""

    def test_valid_ru_accepted(self):
        from core.user_profile import UserProfile
        p = UserProfile(language="ru")
        assert p.language == "ru"

    def test_valid_en_accepted(self):
        from core.user_profile import UserProfile
        p = UserProfile(language="en")
        assert p.language == "en"

    def test_bcp47_zh_cn_accepted(self):
        from core.user_profile import UserProfile
        p = UserProfile(language="zh-CN")
        assert p.language == "zh-cn"  # normalised to lower

    def test_path_traversal_language_raises(self):
        from pydantic import ValidationError
        from core.user_profile import UserProfile
        with pytest.raises(ValidationError, match="BCP-47"):
            UserProfile(language="../etc/passwd")

    def test_script_tag_language_raises(self):
        from pydantic import ValidationError
        from core.user_profile import UserProfile
        with pytest.raises(ValidationError, match="BCP-47"):
            UserProfile(language="<script>alert(1)</script>")

    def test_empty_language_raises(self):
        from pydantic import ValidationError
        from core.user_profile import UserProfile
        with pytest.raises(ValidationError, match="BCP-47"):
            UserProfile(language="")

    def test_language_normalized_to_lowercase(self):
        from core.user_profile import UserProfile
        p = UserProfile(language="RU")
        assert p.language == "ru"

    def test_negative_counter_raises(self):
        from pydantic import ValidationError
        from core.user_profile import UserProfile
        with pytest.raises(ValidationError, match="non-negative"):
            UserProfile(interaction_count=-1)
