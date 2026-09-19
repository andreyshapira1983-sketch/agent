"""The one-inserted-word tolerance must hold for two-word trigger phrases.

Found 2026-08-20 by mutation: `_loose_patterns`'s `if len(words) < 2` moved to
`< 3` — which switches the module's headline feature off for every two-word
term — and the whole suite stayed green.

The tolerance is not decoration. `_matches_approval_status` uses it to SUPPRESS:
a consent request aimed at the agent ("запроси моё подтверждение …") must not
be read as a query against the approval inbox, or the engineering task the
request is attached to is silently swallowed by an approval listing. Two-word
consent stems like "request confirmation" and "запроси подтвержд" are exactly
the ones the mutation disarms.

Finding a phrase that discriminates took measuring rather than guessing. Most
candidates answer False in both worlds — for different reasons: in one the
suppression fires, in the other the text simply reaches no branch at all. Same
answer, different cause, and a test built on those would have proved nothing.
The three below are the ones where the suppression is the only thing standing
between the phrase and a wrong route: each carries approval-inbox context, so
without the tolerance it is classified as an inbox query.
"""
from __future__ import annotations

import pytest

from core.operator_intent_patterns import _matches_approval_status


@pytest.mark.parametrize(
    "text",
    [
        "request explicit confirmation for the pending approval item",
        "запроси наше подтверждение и покажи ожидающие разрешения",
        "запрашивай моё подтверждение по всем заявкам inbox",
    ],
)
def test_a_consent_request_with_one_word_wedged_in_is_not_an_inbox_query(
    text: str,
) -> None:
    assert _matches_approval_status(text) is False, (
        "a request for the operator's consent was routed as an approval-inbox "
        "query — the task attached to it disappears into an approval listing"
    )


def test_the_plain_form_without_a_wedged_word_is_also_suppressed() -> None:
    """Boundary pin: the tolerance must extend the literal match, not replace
    it. If this ever fails the guard stopped working outright."""
    assert _matches_approval_status("request confirmation for the pending approval") is False


def test_a_genuine_inbox_query_still_routes() -> None:
    """The other boundary: suppressing everything would satisfy the tests above
    while breaking the approval inbox entirely."""
    assert _matches_approval_status("покажи ожидающие разрешения") is True
