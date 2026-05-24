"""tests/test_video_tool.py — тесты VideoTool (S5.3.2)"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools.builtins.video_tool import VideoTool

VIDEO = "data/test_video.mp4"
t = VideoTool()


def section(name):
    print(f"\n{'='*50}")
    print(f"  {name}")
    print("="*50)


# ── 1. info ───────────────────────────────────────────────────────────────────
section("1. info")
r = t.execute(action="info", input_path=VIDEO)
print("success:", r.success)
if r.success:
    print(json.dumps(r.output, indent=2, ensure_ascii=False))
else:
    print("ERROR:", r.error)
assert r.success, f"info failed: {r.error}"
assert r.output["video"]["width"] == 320
assert r.output["duration_sec"] > 9


# ── 2. thumbnail ──────────────────────────────────────────────────────────────
section("2. thumbnail")
r = t.execute(
    action="thumbnail",
    input_path=VIDEO,
    output_path="data/test_thumb.jpg",
    timestamp="00:00:03",
    thumbnail_width=160,
)
print("success:", r.success)
print(r.output)
assert r.success, f"thumbnail failed: {r.error}"
assert r.output.get("width") == 160


# ── 3. cut ────────────────────────────────────────────────────────────────────
section("3. cut")
r = t.execute(
    action="cut",
    input_path=VIDEO,
    output_path="data/test_cut.mp4",
    start_time="00:00:02",
    end_time="00:00:05",
)
print("success:", r.success)
print(r.output)
assert r.success, f"cut failed: {r.error}"
assert r.output["size_bytes"] > 0


# ── 4. extract_audio ──────────────────────────────────────────────────────────
section("4. extract_audio")
r = t.execute(
    action="extract_audio",
    input_path=VIDEO,
    output_path="data/test_audio.mp3",
    audio_format="mp3",
)
print("success:", r.success)
print(r.output)
assert r.success, f"extract_audio failed: {r.error}"
assert r.output["size_bytes"] > 0


# ── 5. dry_run cut ────────────────────────────────────────────────────────────
section("5. dry_run cut")
r = t.execute(
    action="cut",
    input_path=VIDEO,
    output_path="data/not_created.mp4",
    start_time="0",
    end_time="5",
    dry_run=True,
)
print("success:", r.success)
print(r.output)
assert r.success
assert "[dry_run]" in r.output
import os
assert not os.path.exists("data/not_created.mp4"), "dry_run must not create file"


# ── 6. error: bad file ────────────────────────────────────────────────────────
section("6. error: nonexistent file")
r = t.execute(action="info", input_path="data/nonexistent.mp4")
print("success:", r.success)
print("error:", r.error)
assert not r.success


print("\n\n✅ All tests passed!")
