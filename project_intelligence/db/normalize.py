"""Canonical normalization for normalized_value_hash.

Uses recursive typed canonical representation for ALL types so that
user data cannot collide with internal encoding of numbers/bools/null.

Wire format (JSON after canonicalize):
  null  -> {"t":"null"}
  bool  -> {"t":"bool","v":true|false}
  int   -> {"t":"int","v":<int>}
  float -> {"t":"float","v":"<.17g decimal string>"}  # NaN/Inf rejected
  str   -> {"t":"str","v":"<NFC + whitespace-collapsed>"}
  list  -> {"t":"list","v":[<canonical>, ...]}
  dict  -> {"t":"dict","v":[[key, value], ...]}  # keys sorted; key collisions raise

SHA-256 of json.dumps(canonical, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True) UTF-8 bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

_WS_RE = re.compile(r"[\t\n\r\f\v ]+")


def _normalize_string(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.strip()
    s = _WS_RE.sub(" ", s)
    return s


def _float_decimal(f: float) -> str:
    if math.isnan(f) or math.isinf(f):
        raise ValueError("NaN/Inf floats are not allowed in normalized values")
    return format(f, ".17g")


def canonicalize(value: Any) -> Any:
    """Return typed canonical structure (never a bare JSON primitive)."""
    if value is None:
        return {"t": "null"}
    if isinstance(value, bool):
        return {"t": "bool", "v": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"t": "int", "v": value}
    if isinstance(value, float):
        return {"t": "float", "v": _float_decimal(value)}
    if isinstance(value, str):
        return {"t": "str", "v": _normalize_string(value)}
    if isinstance(value, dict):
        pairs: list[list[Any]] = []
        seen: set[str] = set()
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"dict keys must be str, got {type(k)}")
            nk = _normalize_string(k)
            if nk in seen:
                raise ValueError(
                    f"dict key collision after normalization: {k!r} -> {nk!r}"
                )
            seen.add(nk)
            pairs.append([nk, canonicalize(v)])
        pairs.sort(key=lambda p: p[0])
        return {"t": "dict", "v": pairs}
    if isinstance(value, (list, tuple)):
        return {"t": "list", "v": [canonicalize(x) for x in value]}
    raise TypeError(f"unsupported type for canonicalize: {type(value)}")


def normalized_value_hash(value: Any) -> str:
    """SHA-256 hex of canonical JSON form of value."""
    canonical = canonicalize(value)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_text(text: str) -> str:
    return content_hash_bytes(text.encode("utf-8"))


def deterministic_id(*parts: str, prefix: str = "") -> str:
    payload = "\0".join(parts)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}{h}" if prefix else h
