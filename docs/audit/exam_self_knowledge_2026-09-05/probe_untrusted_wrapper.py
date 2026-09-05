"""Probe kept beside the exam transcripts (2026-09-05, turn 18).

Does the injection guard's «untrusted content» wrapper blind the synthesizer
model to a file it received whole? Same file, same question, same model
(deepseek-chat, temperature 0), once wrapped by `annotate_suspicious` and once
plain. Run from the repository root; needs the DeepSeek key in `.env`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, ".")
load_dotenv(".env")

from core.answer_format import format_artifact  # noqa: E402 — path set above
from core.injection_guard import annotate_suspicious  # noqa: E402
from core.llm import LLM  # noqa: E402

DOC = Path("knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md")
QUESTION = (
    "Что ты видишь из файла knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md и "
    "сколько знаков? Назови число знаков, дошедших до тебя, и процитируй первую "
    "и последнюю строку из того, что видишь. Отвечай коротко."
)
SYSTEM = "Ты отвечаешь только по приведённым уликам. Не выдумывай."


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    block = format_artifact("file_read", text, question=QUESTION)
    llm = LLM(provider="deepseek", model="deepseek-chat")
    variants = (
        ("wrapped", annotate_suspicious(block, f"file:{DOC.as_posix()}")),
        ("plain", block),
    )
    for label, evidence in variants:
        user = f"Улики:\n<<<\n{evidence}\n>>>\n\nВопрос: {QUESTION}"
        out = llm.complete(system=SYSTEM, user=user, max_tokens=400, temperature=0.0)
        print(f"=== {label} ({len(evidence)} chars of evidence) ===")
        print(out.strip()[:700])
        print()


if __name__ == "__main__":
    main()
