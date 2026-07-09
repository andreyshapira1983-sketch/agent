"""Generate/check a deterministic CycloneDX SBOM from requirements.lock."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.supply_chain import build_cyclonedx_sbom, parse_requirements_lock  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", default="requirements.lock")
    parser.add_argument("--output", default="sbom.cdx.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    lock_path = ROOT / args.lock
    output_path = ROOT / args.output
    payload = build_cyclonedx_sbom(parse_requirements_lock(lock_path))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not output_path.exists():
            print(f"{output_path.relative_to(ROOT)} is missing", file=sys.stderr)
            return 1
        current = output_path.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"{output_path.relative_to(ROOT)} is out of sync with "
                f"{lock_path.relative_to(ROOT)}",
                file=sys.stderr,
            )
            return 1
        print(f"{output_path.relative_to(ROOT)} is in sync")
        return 0

    output_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {output_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
