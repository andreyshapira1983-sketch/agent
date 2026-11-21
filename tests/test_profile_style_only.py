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


class TestPlannerIgnoresProfile:
    def test_planner_source_does_not_import_user_profile(self):
        # Source-level contract: core/planner.py must not import the
        # user_profile module at all. Importing it would mean the
        # planner has access to operator history, which P2 forbids.
        # Through the module (census C1): the rule is about what the PLANNER
        # may import, and it holds wherever the planner lives.
        import inspect

        from core import planner as _planner_mod

        src = inspect.getsource(_planner_mod)
        assert "from core.user_profile" not in src
        assert "import core.user_profile" not in src
