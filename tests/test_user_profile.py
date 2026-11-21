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

from pathlib import Path

import pytest

from core.user_profile import (
    UserProfile,
    UserProfileStore,
    update_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh() -> UserProfile:
    return UserProfile()


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


# ---------------------------------------------------------------------------
# 4. Expertise stays intermediate without strong signals
# ---------------------------------------------------------------------------

class TestIntermediateExpertise:
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
        "отвечай подробнее",
        "говори подробнее",
        "пиши подробнее",
        "объясняй подробнее",
    ])
    def test_directive_подробнее_locks_verbosity(self, text: str) -> None:  # noqa: PLC2401 — the Russian directive word is the subject of this test
        """Communication-verb + 'подробн*' is an explicit verbosity directive."""
        p = update_profile(_fresh(), text)
        assert p.verbosity == "detailed"
        assert p.verbosity_locked is True


# ---------------------------------------------------------------------------
# 12. Integration: AgentLoop with user_profile_store
# ---------------------------------------------------------------------------

class TestAgentLoopUserProfileIntegration:
    """Verify that AgentLoop accepts user_profile_store and emits profile events."""

    def _make_loop(self, tmp_path: Path):
        """Build a minimal AgentLoop with a real UserProfileStore and a mock LLM."""
        from unittest.mock import MagicMock

        from core.logger import TraceLogger
        from core.loop import AgentLoop
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

    def test_restarted_loop_first_turn_reads_the_stored_profile(
        self, tmp_path: Path
    ) -> None:
        """The open-run load exists for exactly one consumer set: turn one.

        Accumulation does NOT need it — the store reloads itself when
        ``base=None`` (update_from_interaction), and within one instance the
        tail refreshes the field. Measured 2026-08-08: cutting the load left
        interaction counts intact everywhere. What the cut actually breaks is
        a restarted agent's FIRST turn: the stored style profile never reaches
        the synthesizer prompt (and the language bridge gets None). This test
        pins that consumer: a fresh loop over an existing profile must show
        the stored style to the very first synthesis call.
        """
        from core.user_profile import UserProfile

        loop1, store = self._make_loop(tmp_path)
        store.save(
            UserProfile(
                expertise="expert",
                technical=True,
                verbosity="brief",
                language="ru",
                interaction_count=7,
            )
        )
        del loop1  # the restart: a NEW loop instance over the same disk

        loop2, _ = self._make_loop(tmp_path)
        loop2.run("hello world")

        joined = " || ".join(
            str(call) for call in loop2.llm.complete.call_args_list
        )
        assert "<user_profile>" in joined, (
            "the first turn after a restart never showed the stored profile "
            "to the model — the open-run load is the only thing that feeds it"
        )
        assert "expertise: expert" in joined and "verbosity: brief" in joined


# ============================================================
# Security / validation tests (audit fixes)
# ============================================================

class TestUserProfileLanguageValidation:
    """Tests for the language field BCP-47 validator added in deep audit."""


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


    def test_negative_counter_raises(self):
        from pydantic import ValidationError

        from core.user_profile import UserProfile
        with pytest.raises(ValidationError, match="non-negative"):
            UserProfile(interaction_count=-1)
