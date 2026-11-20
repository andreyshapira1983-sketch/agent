"""Universal, non-reversible redaction of secrets and PII for logs, LLM prompts and user-facing output."""
from __future__ import annotations

from typing import Any

from core.dlp import DlpFinding, pii_replacement, scan_pii
from core.secret_scanner import SecretFinding, is_credential_key, scan


def _replacement(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def redact_text(text: str) -> tuple[str, list[SecretFinding]]:
    """Replace every secret span with a `[REDACTED:<kind>]` token."""
    if not isinstance(text, str) or not text:
        return text, []

    findings = scan(text)
    if not findings:
        return text, []

    # widest match at each start offset wins; replace right-to-left so offsets stay valid
    findings_sorted = sorted(findings, key=lambda f: (f.start, -f.end))
    kept: list[SecretFinding] = []
    last_end = -1
    for f in findings_sorted:
        # skip a finding already covered by a wider match
        if f.start < last_end:
            continue
        kept.append(f)
        last_end = f.end

    out = text
    for f in sorted(kept, key=lambda f: f.start, reverse=True):
        out = out[: f.start] + _replacement(f.kind) + out[f.end :]
    return out, kept


def redact_dlp_text(
    text: str,
) -> tuple[str, list[SecretFinding], list[DlpFinding]]:
    """Redact both credentials and sensitive PII from `text`.

    `redact_text` remains the secret-only primitive for compatibility.
    Boundary surfaces should call this function so email / phone / SSN
    values do not reach logs, LLM prompts, output, or durable memory raw.
    """
    if not isinstance(text, str) or not text:
        return text, [], []

    secret_findings = scan(text)
    pii_findings = scan_pii(text)
    if not secret_findings and not pii_findings:
        return text, [], []

    replacements: list[tuple[int, int, str, str, Any]] = []
    for finding in secret_findings:
        replacements.append(
            (
                finding.start,
                finding.end,
                _replacement(finding.kind),
                "secret",
                finding,
            )
        )
    for finding in pii_findings:
        replacements.append(
            (
                finding.start,
                finding.end,
                pii_replacement(finding.kind),
                "pii",
                finding,
            )
        )

    replacements.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    kept: list[tuple[int, int, str, str, Any]] = []
    last_end = -1
    for item in replacements:
        start, end, _replacement_text, _category, _finding = item
        if start < last_end:
            continue
        kept.append(item)
        last_end = end

    out = text
    kept_secrets: list[SecretFinding] = []
    kept_pii: list[DlpFinding] = []
    for start, end, replacement_text, category, finding in sorted(
        kept, key=lambda item: item[0], reverse=True
    ):
        out = out[:start] + replacement_text + out[end:]
        if category == "secret":
            kept_secrets.append(finding)
        else:
            kept_pii.append(finding)
    kept_secrets.sort(key=lambda item: item.start)
    kept_pii.sort(key=lambda item: item.start)
    return out, kept_secrets, kept_pii


def redact_payload(obj: Any) -> Any:
    """Deep-walk a logging payload, redacting every string field.

    Mapping keys are also read as evidence about their value; keys themselves are never rewritten.
    """
    if isinstance(obj, str):
        red, _secret_findings, _pii_findings = redact_dlp_text(obj)
        return red
    if isinstance(obj, dict):
        out_map: dict[Any, Any] = {}
        for key, value in obj.items():
            redacted = redact_payload(value)
            # fallback only: mask by name when the value revealed nothing, so recognisable secrets keep their kind; empty values stay
            if (
                isinstance(value, str)
                and value.strip()
                and redacted == value
                and is_credential_key(key)
            ):
                redacted = _replacement("credential-assignment")
            out_map[key] = redacted
        return out_map
    if isinstance(obj, (list, tuple)):
        redacted = [redact_payload(x) for x in obj]
        return tuple(redacted) if isinstance(obj, tuple) else redacted
    # sets and bytes must not reach the logs raw
    if isinstance(obj, (set, frozenset)):
        # a list, not a set: two different secrets redact to the same label and a set would drop one
        return [redact_payload(x) for x in sorted(obj, key=repr)]
    if isinstance(obj, (bytes, bytearray)):
        # decode with replacement: the log needs a readable trace, not exact bytes
        return redact_payload(bytes(obj).decode("utf-8", errors="replace"))
    return obj


def collect_pii_findings(text: str) -> list[DlpFinding]:
    """Convenience for callers that need PII findings without rewriting text."""
    if not isinstance(text, str):
        return []
    return scan_pii(text)


def prepare_text_for_llm_boundary(text: str) -> tuple[str, dict[str, Any]]:
    """Redact secrets and sensitive PII before text is assembled into an LLM prompt.

    Reuses ``redact_dlp_text`` — no new scanner/DLP rules. Returns
    ``(safe_text, log_payload)``; *log_payload* is empty when nothing was redacted.
    """
    if not isinstance(text, str):
        return "", {}
    safe, secret_findings, pii_findings = redact_dlp_text(text)
    if not secret_findings and not pii_findings:
        return safe, {}
    return safe, {
        "redaction": True,
        "secret_count": len(secret_findings),
        "pii_count": len(pii_findings),
        "secret_kinds": sorted({f.kind for f in secret_findings}),
        "pii_kinds": sorted({f.kind for f in pii_findings}),
    }
