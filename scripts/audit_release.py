"""CI-friendly release and supply-chain gate.

This script intentionally avoids constructing the agent or LLM adapters, so it
can run on GitHub Actions without model API keys.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.release_hygiene import build_release_manifest  # noqa: E402
from core.supply_chain import audit_supply_chain  # noqa: E402


def main() -> int:
    release = build_release_manifest(ROOT).report()
    supply_chain = audit_supply_chain(ROOT)
    payload = {
        "release_hygiene": release.to_dict(),
        "supply_chain": supply_chain.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if release.ok and supply_chain.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
