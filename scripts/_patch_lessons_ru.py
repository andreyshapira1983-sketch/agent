"""Patch existing reflection lessons to add Russian tag synonyms."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from core.state_integrity import encode_state_row  # noqa: E402

p = pathlib.Path("data/persistent_memory.jsonl")
lines = p.read_text("utf-8").splitlines()
updated = []
changed = 0

for line in lines:
    if not line.strip():
        continue
    row = json.loads(line)
    pl = row.get("payload", {})
    tags = pl.get("tags", [])
    if "reflection" in tags and "урок" not in tags:
        tags = tags + ["урок", "рефлексия"]
        pl["tags"] = tags
        row = json.loads(encode_state_row(pl))
        changed += 1
    updated.append(json.dumps(row, ensure_ascii=False))

p.write_text("\n".join(updated) + "\n", encoding="utf-8")
print(f"Updated {changed} reflection lessons with Russian tags")
