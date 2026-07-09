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


# ---------------------------------------------------------------------------
# ИНН: checksum + context filtering. The naive `\b\d{10}\b` regex would
# fire on every 10-digit unix timestamp, build number, or hash slice. We
# require both the Russian checksum AND a non-suppressing context window.
# ---------------------------------------------------------------------------

# Sberbank ИНН — public legal-entity tax id, valid per ФНС checksum.
VALID_INN_10 = "7707083893"
# Synthetic 12-digit ИНН satisfying both control digits (verified manually).
VALID_INN_12 = "500100732259"


def test_inn_real_value_detected():
    findings = scan_pii(f"ИНН: {VALID_INN_10}")
    assert {f.kind for f in findings} == {"inn"}
    assert findings[0].matched == VALID_INN_10


def test_inn_individual_12_digit_detected():
    findings = scan_pii(f"налогоплательщик {VALID_INN_12}")
    assert {f.kind for f in findings} == {"inn"}


def test_unix_timestamp_is_not_inn():
    # Real unix seconds for 2024-2026 are 10-digit values starting with 17.
    # They must not be flagged as ИНН.
    for ts in ("1717419600", "1780000000", "1700000000"):
        markers = pii_markers(f"event at unix={ts} processed")
        assert "inn" not in markers, ts


def test_unix_timestamp_in_repr_dict_not_inn():
    # The shape produced by the current_time tool: a python-repr dict
    # with `'unix': 1780000000` — must not raise PII.
    repr_dict = (
        "{'iso_utc': '2026-06-03T12:47:35+00:00', 'weekday': 'Wednesday', "
        "'unix': 1780000000}"
    )
    assert "inn" not in pii_markers(repr_dict)


def test_random_10_digit_id_without_checksum_not_inn():
    # Build numbers / hashes that do not satisfy the ФНС checksum
    # are never reported as ИНН regardless of surrounding text.
    assert "inn" not in pii_markers("build 1234567890 deployed")


def test_inn_checksum_valid_overrides_negative_context_when_label_present():
    # When the explicit label "ИНН" is present, suppression by negative
    # tokens like "build" must not hide the true positive.
    findings = scan_pii(f"build pipeline; clientИНН={VALID_INN_10} ok")
    assert "inn" in {f.kind for f in findings}


def test_inn_in_neutral_text_with_valid_checksum_is_detected():
    # No suppressing context, valid checksum -> PII detected.
    assert "inn" in pii_markers(f"Контрагент {VALID_INN_10} оплатил счёт.")
