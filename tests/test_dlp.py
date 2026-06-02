"""DLP helpers for PII detection and masking."""
from __future__ import annotations

from core.dlp import contains_pii, pii_markers, scan_pii


def test_scan_pii_finds_supported_markers():
    text = "Email andre@example.com, SSN 123-45-6789, phone +1 415 555 1234."

    findings = scan_pii(text)

    assert {finding.kind for finding in findings} == {"email", "ssn", "phone"}
    assert all(finding.matched for finding in findings)


def test_pii_markers_are_unique_and_sorted():
    markers = pii_markers("a@example.com b@example.com +972 50 123 4567")

    assert markers == ["email", "phone"]


def test_contains_pii_reports_reasons():
    found, reasons = contains_pii("Contact andre@example.com")

    assert found is True
    assert reasons == ["PII markers: ['email']"]


def test_clean_text_has_no_pii():
    found, reasons = contains_pii("Python 3.14.5 on Windows")

    assert found is False
    assert reasons == []
