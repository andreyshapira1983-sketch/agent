"""The model-selection admission gate had no witness at all.

Found by mutation 2026-08-20. `ModelSpec.selectable` opens with

    if not self.enabled or not self.provider_supported():
        return False

and turning that `return False` into `return True` survived the ENTIRE suite.
Inverted, the line does three things at once: a switched-off spec becomes
selectable, a spec whose provider is outside SUPPORTED_PROVIDERS becomes
selectable, and — because the early return skips the rest of the method — the
`require_available` check is bypassed for both.

That gate is load-bearing right now: an unauthorised provider stays out
because it is not in SUPPORTED_PROVIDERS, and on the mutant such a spec
reports selectable=True.

Nothing in the repo asserted any of it. These tests are about the rule, not
about today's registry contents. Authorising a provider, disabling a spec, or
changing which models exist must all stay cheap — what may not become cheap is
a spec reaching selection without passing the gate at all.
"""
from __future__ import annotations

from core.model_router import ModelSpec


def _spec(**kw) -> ModelSpec:
    base = {"id": "probe", "provider": "anthropic", "model": "m"}
    base.update(kw)
    return ModelSpec(**base)


def test_a_disabled_spec_is_never_selectable() -> None:
    spec = _spec(enabled=False)
    assert spec.selectable(allow_mock=False, require_available=True) is False
    assert spec.selectable(allow_mock=True, require_available=False) is False
