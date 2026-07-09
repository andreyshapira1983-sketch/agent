"""P2: profile influences ONLY presentation style, never domain.

Boundary tests for the operator-profile contract. The fields the
profile carries about *who the user is* (expertise, verbosity,
vocabulary, language) may steer the LLM's tone and depth — but the
fields tied to *what the user has talked about before* (interests,
interaction_count) must not reach the synthesizer prompt and must not
influence planning, source selection, or memory retrieval.

These tests guard against the regression where past-topic history is
used to narrow the current answer's scope.
"""
from __future__ import annotations

import pytest

from core.user_profile import UserProfile, profile_to_prompt_block


class TestProfilePromptBlockStyleOnly:
    def test_block_lists_style_fields_only(self):
        p = UserProfile(
            expertise="expert",
            verbosity="brief",
            technical=True,
            language="en",
            interests=["python", "security", "machine_learning"],
            interaction_count=99,
        )
        block = profile_to_prompt_block(p)
        # Style fields ARE present.
        assert "expertise: expert" in block
        assert "verbosity: brief" in block
        assert "vocabulary: technical" in block
        assert "language: en" in block
        # Domain-tied fields ARE NOT present.
        assert "interests" not in block.lower()
        assert "python" not in block
        assert "security" not in block
        assert "machine_learning" not in block
        assert "interaction_count" not in block.lower()
        assert "99" not in block

    @pytest.mark.parametrize("interests", [
        [],
        ["a"],
        ["a", "b", "c", "d", "e"],
    ])
    def test_block_size_constant_under_interests(self, interests):
        # Block length must not grow with the size of the interests list
        # — otherwise an attacker / accidental signal in past topics
        # could expand the prompt and affect attention.
        p1 = UserProfile(interests=[])
        p2 = UserProfile(interests=interests)
        assert len(profile_to_prompt_block(p1)) == len(profile_to_prompt_block(p2))


class TestPlannerIgnoresProfile:
    def test_planner_user_prompt_does_not_reference_profile(self):
        # Static contract: the planner's prompt builder must never
        # embed profile fields. We instantiate a real LLMPlanner with
        # a minimal stub LLM/registry and inspect the produced prompt.
        from unittest.mock import MagicMock

        from core.planner import LLMPlanner

        registry = MagicMock()
        registry.list.return_value = []
        planner = LLMPlanner(llm=MagicMock(), registry=registry)
        prompt = planner._build_user_prompt(
            question="Calculate the orbital period of Mercury.",
            file_hint=None,
        )
        for needle in ("user_profile", "interests:", "expertise:", "verbosity:"):
            assert needle not in prompt, f"planner prompt leaked {needle!r}"

    def test_planner_source_does_not_import_user_profile(self):
        # Source-level contract: core/planner.py must not import the
        # user_profile module at all. Importing it would mean the
        # planner has access to operator history, which P2 forbids.
        import pathlib
        src = pathlib.Path("core/planner.py").read_text(encoding="utf-8")
        assert "from core.user_profile" not in src
        assert "import core.user_profile" not in src


class TestMemoryRetrievalIgnoresProfile:
    def test_memory_select_uses_question_not_interests(self):
        # MemoryRetrievalPolicy.select must score by question/tag overlap
        # alone. Even if the user's "interests" included terms unrelated
        # to the current question, retrieval ranking must be unaffected.
        from core.memory_policy import MemoryRetrievalPolicy
        from core.models import MemoryRecord

        policy = MemoryRetrievalPolicy()
        records = [
            MemoryRecord(
                content="Mercury orbital period 88 days",
                tags=["astronomy"],
            ),
            MemoryRecord(
                content="Python list comprehension syntax",
                tags=["python"],
            ),
        ]
        # Question is purely astronomy. Profile interests are irrelevant
        # because the policy.select signature does not accept profile.
        selected = policy.select(records, question="What is Mercury's orbital period?")
        # The astronomy record should rank ahead of the python one,
        # regardless of any past interest in python.
        assert selected[0].content.startswith("Mercury")


class TestSourceRankerIgnoresProfile:
    def test_rank_chain_signature_excludes_profile(self):
        # Negative contract: the source-ranker public API must not
        # accept a profile/interests argument; if a future refactor
        # tries to add one, this test fails fast.
        import inspect

        from core.source_ranker import rank_chain

        sig = inspect.signature(rank_chain)
        for forbidden in ("profile", "user_profile", "interests"):
            assert forbidden not in sig.parameters, (
                f"rank_chain must not accept {forbidden!r} parameter"
            )


class TestSystemAnswerStyleOnlyClause:
    def test_system_answer_states_profile_is_style_only(self):
        # The synthesizer-system prompt must explicitly tell the LLM
        # to use the user_profile block for style only and never for
        # topic / domain selection. This is the LLM-level guardrail.
        from core.loop import SYSTEM_ANSWER

        text = SYSTEM_ANSWER.lower()
        assert "user_profile" in text or "user profile" in text
        # Either a "style only" phrase or an explicit "must not"
        # against domain influence is required.
        assert ("style only" in text) or ("only for presentation" in text)
        assert ("not influence" in text) or ("must not" in text) or ("never" in text)
