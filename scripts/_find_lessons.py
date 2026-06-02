import json, pathlib

for fname in ["data/persistent_memory.jsonl", "data/episodic_memory.jsonl"]:
    p = pathlib.Path(fname)
    if not p.exists():
        print(f"NOT FOUND: {fname}")
        continue
    count = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        count += 1
        r = json.loads(line)
        tags = r.get("tags", [])
        if "reflection" in str(tags) or "lesson" in str(tags) or r.get("type") == "episodic":
            print(f"{fname}  id={r.get('id')}  type={r.get('type')}  tags={tags}")
    print(f"  total lines: {count}")
