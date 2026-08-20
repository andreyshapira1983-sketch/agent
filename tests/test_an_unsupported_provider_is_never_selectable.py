"""The model-selection admission gate had no witness at all.

Found by mutation 2026-08-20. `ModelSpec.selectable` opens with

    if not self.enabled or not self.provider_supported():
        return False

and turning that `return False` into `return True` survived the ENTIRE suite.
Inverted, the line does three things at once: a switched-off spec becomes
selectable, a spec whose provider is outside SUPPORTED_PROVIDERS becomes
selectable, and — because the early return skips the rest of the method — the
`require_available` check is bypassed for both.

That gate is load-bearing right now. `deepseek` is not in SUPPORTED_PROVIDERS,
which is precisely what keeps a DeepSeek spec from being chosen while the
operator has not authorised it. Measured on the mutant: a spec with
provider="deepseek" reports selectable=True.

Nothing in the repo asserted any of it. These tests are about the rule, not
about today's registry contents, so adding a provider to the supported set
does not redden them — only losing the rule does.
"""
from __future__ import annotations

import pytest

from core.model_router import SUPPORTED_PROVIDERS, ModelSpec


def _spec(**kw) -> ModelSpec:
    base = {"id": "probe", "provider": "anthropic", "model": "m"}
    base.update(kw)
    return ModelSpec(**base)


def test_a_provider_outside_the_supported_set_is_never_selectable() -> None:
    assert "deepseek" not in SUPPORTED_PROVIDERS, (
        "this test's example provider joined the supported set — pick another "
        "unsupported name rather than deleting the case"
    )
    spec = _spec(provider="deepseek", requires_env=("DEEPSEEK_API_KEY",))
    assert spec.selectable(allow_mock=False, require_available=True) is False
    assert spec.selectable(allow_mock=True, require_available=False) is False, (
        "an unsupported provider became selectable once availability was not "
        "required — the provider check must not be reachable past that switch"
    )


def test_a_disabled_spec_is_never_selectable() -> None:
    spec = _spec(enabled=False)
    assert spec.selectable(allow_mock=False, require_available=True) is False
    assert spec.selectable(allow_mock=True, require_available=False) is False


def test_a_missing_environment_variable_blocks_selection_when_required() -> None:
    spec = _spec(provider="openai", requires_env=("NO_SUCH_KEY_QQZZ_8811",))
    assert spec.selectable(allow_mock=False, require_available=True) is False
    assert spec.selectable(allow_mock=False, require_available=False) is True, (
        "require_available=False is the documented way to plan without keys; "
        "if this is False the gate refuses more than it should"
    )


def test_mock_needs_permission_but_a_normal_spec_does_not() -> None:
    """Boundary pin: a gate that refused everything would satisfy the tests
    above while making the router unable to choose any model."""
    assert _spec().selectable(allow_mock=False, require_available=True) is True
    mock = _spec(provider="mock")
    assert mock.selectable(allow_mock=False, require_available=True) is False
    assert mock.selectable(allow_mock=True, require_available=True) is True


@pytest.mark.parametrize("provider", sorted(SUPPORTED_PROVIDERS - {"mock"}))
def test_every_supported_provider_can_still_be_chosen(provider: str) -> None:
    assert _spec(provider=provider).selectable(
        allow_mock=False, require_available=True
    ) is True
