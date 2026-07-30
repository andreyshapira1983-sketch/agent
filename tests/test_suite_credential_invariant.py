"""The suite must never depend on a real provider credential.

`AgentLoop` only builds when `core.model_router._provider_has_credentials`
finds a provider's env vars set, so without one a handful of tests fail for an
environmental reason rather than a real defect. `tests/conftest.py` closes that
with `_ensure_placeholder_credential`, which fakes a credential when the machine
has none.

These tests lock that invariant in place. Delete the fixture and they go red on
a clean checkout — which is exactly the state a new contributor is in, and the
state a fork PR runs in because GitHub denies it repository secrets.
"""
from __future__ import annotations

import os

import pytest

from core.model_router import _DEFAULT_PROVIDER_ENV, _provider_has_credentials
from tests.conftest import (
    _PLACEHOLDER_CREDENTIAL,
    _ensure_placeholder_credential,
    _real_credential_present,
)

# Every credential env var any real provider reads, taken from the router so
# this list cannot drift away from it.
_ALL_PROVIDER_ENV_VARS = sorted(
    {var for required in _DEFAULT_PROVIDER_ENV.values() for var in required}
)

# The fixture body, unwrapped from pytest's fixture decorator so a test can call
# it directly against a controlled environment.
_fixture_body = _ensure_placeholder_credential.__wrapped__


@pytest.fixture
def bare_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A process that looks like a fresh clone: no provider credentials at all."""
    for var in _ALL_PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert not _real_credential_present()
    return monkeypatch


def test_a_provider_credential_is_always_visible_to_the_suite() -> None:
    """Whatever the machine looks like, a test can build `AgentLoop`."""
    assert _real_credential_present(), (
        "no provider credential reached the test process — `AgentLoop` cannot "
        "be built, so tests will fail for an environmental reason"
    )


def test_fixture_supplies_a_credential_when_the_machine_has_none(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """The no-credential path, exercised directly.

    The invariant test above passes for free on any machine that already has a
    key, so it cannot tell a working fixture from a deleted one. This can.
    """
    _fixture_body(bare_environment)

    assert _real_credential_present()
    assert os.environ["ANTHROPIC_API_KEY"] == _PLACEHOLDER_CREDENTIAL


def test_a_half_configured_provider_still_gets_a_placeholder(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """A partially configured provider is not a credential.

    `local` needs both `LOCAL_LLM_BASE_URL` and `LOCAL_LLM_MODEL`. Treating the
    presence of any one variable as "this machine is credentialed" would skip
    the placeholder and leave the suite with nothing usable — the exact failure
    the fixture exists to prevent.
    """
    bare_environment.setenv("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
    assert not _provider_has_credentials("local")

    _fixture_body(bare_environment)

    assert os.environ["ANTHROPIC_API_KEY"] == _PLACEHOLDER_CREDENTIAL


def test_a_whitespace_only_key_is_not_a_credential(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """The router strips before testing, so the fixture must agree with it."""
    bare_environment.setenv("ANTHROPIC_API_KEY", "   ")

    _fixture_body(bare_environment)

    assert os.environ["ANTHROPIC_API_KEY"] == _PLACEHOLDER_CREDENTIAL


def test_a_real_credential_takes_precedence(
    bare_environment: pytest.MonkeyPatch,
) -> None:
    """The fixture defers to reality instead of overwriting it.

    This is what keeps CI running under its real secrets, and what stops the
    fixture from silently redirecting an operator's own routing.
    """
    bare_environment.setenv("ANTHROPIC_API_KEY", "operator-supplied")

    _fixture_body(bare_environment)

    assert os.environ["ANTHROPIC_API_KEY"] == "operator-supplied"


def test_placeholder_is_not_a_usable_credential() -> None:
    """The stand-in must fail loudly if it ever reached a real client.

    A placeholder that looked like a valid key could hide a test that really
    does call out to a provider; one that plainly cannot authenticate cannot.
    """
    assert "placeholder" in _PLACEHOLDER_CREDENTIAL
    assert not _PLACEHOLDER_CREDENTIAL.startswith("sk-")
