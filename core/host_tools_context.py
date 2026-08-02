"""Host-tool context: which desktop tools exist and when to mention them.

The single source of truth for operator-machine tools (Blender, OpenSCAD,
ffmpeg, ...): the relevance gate that decides whether a question warrants
mentioning them, and the prompt block describing them to the planner.
Used by both the planner (system-prompt suffix) and the loop (synthesis
critique). Pure environment/text inspection; moved verbatim from
core/planner.py.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable


# Single source of truth for host-tool names (env var, tool name, description).
# Reused by `_build_host_tools_block` and `host_tools_relevant` so the two never
# drift apart.
_HOST_ENV_TOOLS = [
    ("BLENDER_PATH",   "blender",   "3D rendering / animation (Blender)"),
    ("OPENSCAD_PATH",  "openscad",  "parametric 3D modelling (OpenSCAD)"),
    ("ADB_PATH",       "adb",       "Android device bridge (ADB)"),
    ("FFMPEG_PATH",    "ffmpeg",    "video / audio processing (FFmpeg)"),
    ("MAGICK_PATH",    "magick",    "image processing (ImageMagick)"),
    ("PANDOC_PATH",    "pandoc",    "document conversion (Pandoc)"),
    ("SOFFICE_PATH",   "soffice",   "office documents (LibreOffice)"),
    ("PYTHON_PATH",    "python",    "Python interpreter (custom path)"),
]

# Whole-word task cues that mean "this turn is about producing a document / media
# / device artifact" even when no tool is named explicitly.
_HOST_TASK_WORDS = (
    "document", "word", "excel", "spreadsheet", "pdf", "slides", "presentation",
    "docx", "xlsx", "pptx", "3d", "render", "modelling", "modeling",
    "video", "audio", "image", "convert", "android", "office", "libreoffice",
)

# Effect tools whose use means a host-tool run command may be worth stating.
_HOST_EFFECT_TOOLS = frozenset({"shell_exec", "file_write"})

_HOST_RELEVANCE_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(name) for _env, name, _desc in _HOST_ENV_TOOLS
    ) + "|" + "|".join(re.escape(w) for w in _HOST_TASK_WORDS) + r")\b",
    re.IGNORECASE,
)


def host_tools_relevant(text: str, tools_used: Iterable[str] = ()) -> bool:
    """Deterministic gate: is this turn actually about host tools?

    True when the text (question + planner reasoning) mentions a host-tool name
    or a document/media/device task word (whole-word, so "word" inside "keyword"
    does NOT fire), OR the plan actually used an effect tool (``shell_exec`` /
    ``file_write``). No LLM. Used to avoid injecting ``.env`` host paths into
    every synthesizer prompt (LPF-001 iteration 1b).
    """
    if _HOST_RELEVANCE_RE.search(text or ""):
        return True
    used = {str(t).strip().lower() for t in (tools_used or ()) if t}
    return bool(used & _HOST_EFFECT_TOOLS)


def _build_host_tools_block() -> str:
    """Read tool paths from env vars (loaded from .env) and return a planner
    context block listing what is actually installed on this host.
    Only includes vars that are set and non-empty.
    """
    _ENV_TOOLS = _HOST_ENV_TOOLS
    # Also detect Python from common Windows locations if PYTHON_PATH not set
    found: list[str] = []
    for env_var, tool_name, description in _ENV_TOOLS:
        path = os.environ.get(env_var, "").strip()
        if path and os.path.exists(path):
            found.append(f"  {tool_name:12s} → {path}  ({description})")
        elif path:
            # Path configured but file not found at that location
            found.append(f"  {tool_name:12s} → {path}  ({description}) [path configured but not verified]")

    # Auto-detect Python if not already found via PYTHON_PATH
    if not any("python" in line for line in found):
        username = os.environ.get("USERNAME", "")
        common_python = [
            rf"C:\Users\{username}\AppData\Local\Programs\Python\Python311\python.exe",
            rf"C:\Users\{username}\AppData\Local\Programs\Python\Python312\python.exe",
            r"C:\Python311\python.exe",
            r"C:\Python312\python.exe",
        ]
        for p in common_python:
            if os.path.exists(p):
                found.append(f"  python       → {p}  (Python interpreter, NOT in PATH but installed)")
                break

    if not found:
        return ""

    lines = ["", "Host tools — INSTALLED ON THIS MACHINE (from .env config):"]
    lines += found
    lines += [
        "When the user asks about tasks involving these tools, tell them the tool IS",
        "available at the path shown above. You cannot call these tools directly via",
        "shell_exec (they are outside the workspace sandbox), but you CAN:",
        "  • Write a Python/batch script to the workspace and tell the user to run it",
        "  • Reference the exact path so the user can invoke the tool themselves",
        "  • For Python: write a .py script; user runs it with the Python path above",
        "CRITICAL: When writing any script that uses a tool from this list, ALWAYS",
        "add as the FIRST LINE of the file a comment with the exact run command, e.g.:",
        "  # Run: \"C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe\" --background --python animate_ball.py",
        "  # Run: \"C:\\Users\\andre\\AppData\\Local\\Programs\\Python\\Python311\\python.exe\" script.py",
        "This ensures the user knows the exact command to execute the script.",
    ]
    return "\n".join(lines)
