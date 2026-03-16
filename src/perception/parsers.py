"""
Parsers: JSON, tables, API responses. MVP: placeholder.
"""
from __future__ import annotations

import json
from typing import Any


def parse_json(text: str) -> Any:
    return json.loads(text)
