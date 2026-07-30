"""The suite must never depend on a real provider credential.

`AgentLoop` only builds when `core.model_router._provider_has_credentials`
finds a provider env var set, so without one a handful of tests fail for an
environmental reason rather than a real defect. `tests/conftest.py` closes that
with `_ensure_placeholder_credential`, which fakes a credential when the machine
has none.

These tests lock that invariant in place. Delete the fixture and the first one
goes red on a clean checkout — which is exactly the state a new contributor is
in, and the state a fork PR runs in because GitHub denies it repository secrets.
"""
from __future__ import annotations

import os

from tests.conftest import (
    _PLACEHOLDER_CREDENTIAL,
    _PROVIDER_CREDENTIAL_ENV_VARS,
)


def test_a_provider_credential_is_always_visible_to_the_suite() -> None:
    """Whatever the machine looks like, a test can build `AgentLoop`."""
    assert any(os.environ.get(var) for var in _PROVIDER_CREDENTIAL_ENV_VARS), (
        "no provider credential reached the test process — `AgentLoop` cannot "
        "be built, so tests will fail for an environmental reason"
    )


def test_placeholder_is_not_a_usable_credential() -> None:
    """The stand-in must fail loudly if it ever reached a real client.

    A placeholder that looked like a valid key could hide a test that really
    does call out to a provider; one that plainly cannot authenticate cannot.
    """
    assert "placeholder" in _PLACEHOLDER_CREDENTIAL
    assert not _PLACEHOLDER_CREDENTIAL.startswith("sk-")


def test_a_real_credential_takes_precedence(monkeypatch) -> None:
    """The fixture defers to reality instead of overwriting it.

    This is what keeps CI running under its real secrets, and what stops the
    fixture from silently redirecting an operator's own routing.
    """
    from tests.conftest import _ensure_placeholder_credential

    monkeypatch.setenv("ANTHROPIC_API_KEY", "operator-supplied")
    fixture = _ensure_placeholder_credential.__wrapped__
    fixture(monkeypatch)

    assert os.environ["ANTHROPIC_API_KEY"] == "operator-supplied"
