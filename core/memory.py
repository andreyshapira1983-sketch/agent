"""Working Memory (§4 Memory & Knowledge Governance — short-term, session-scoped).

MVP-4 scope:
  - Stores Turn objects (question, planner reasoning, tools used, answer).
  - Caches tool artifacts by (tool, arguments) so the Executor can avoid
    repeat I/O within one session.
  - Exposes a `conversation_context()` block for injection into the planner
    and synthesizer prompts.
  - Strict bounds: max_turns + max_chars so the prompt cannot grow unbounded.

Not in MVP-4 (intentional):
  - Persistence across runs (that is MVP-5: Persistent Memory + Write Policy).
  - Semantic / episodic / procedural memory.
  - Embeddings, RAG, summarization of old turns.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from core.ids import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Turn:
    """One full Observe -> Respond cycle, as remembered after the fact."""

    index: int
    id: str
    timestamp: datetime
    question: str
    planner_reasoning: str
    tools_used: list[str]
    artifact_labels: list[str]
    answer: str

    def summary(self, max_chars: int = 500) -> str:
        """Compact representation for prompts."""
        ans = self.answer.strip().replace("\n", " ")
        if len(ans) > max_chars:
            ans = ans[: max_chars - 1].rstrip() + "…"
        tools = ", ".join(self.tools_used) if self.tools_used else "(none)"
        return (
            f"Turn {self.index}:\n"
            f"  user: {self.question}\n"
            f"  tools_used: {tools}\n"
            f"  agent_answer: {ans}"
        )


class WorkingMemory:
    """Session-scoped working memory.

    Two views:
      - `turns`: chronological conversation log for prompt injection.
      - `artifacts`: cache of tool outputs keyed by (tool_name, arguments).
    """

    def __init__(self, max_turns: int = 10, max_context_chars: int = 8_000,
                 max_context_tokens: int = 2_000, max_artifacts: int = 200):
        self.session_id: str = new_id("sess")
        self.created_at: datetime = _now()
        self.turns: list[Turn] = []
        self.artifacts: dict[str, dict[str, Any]] = {}  # cache_key -> {tool, arguments, output, turn_index}
        self.max_turns = max_turns
        self.max_context_chars = max_context_chars
        self.max_context_tokens = max_context_tokens
        self.max_artifacts = max_artifacts
        # Monotonic turn counter — survives bounded-retention trimming.
        # Without this, dropping oldest turns reused indices.
        self._next_turn_index: int = 1
        self._cache_lock = threading.Lock()  # serialises cache_store calls from parallel step threads

    # ---------- turn log ----------

    def record_turn(
        self,
        question: str,
        planner_reasoning: str,
        tools_used: Iterable[str],
        artifact_labels: Iterable[str],
        answer: str,
    ) -> Turn:
        turn = Turn(
            index=self._next_turn_index,
            id=new_id("turn"),
            timestamp=_now(),
            question=question,
            planner_reasoning=planner_reasoning,
            tools_used=list(tools_used),
            artifact_labels=list(artifact_labels),
            answer=answer,
        )
        self._next_turn_index += 1
        self.turns.append(turn)
        # Bounded retention — drop oldest if over the cap.
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns :]
        return turn

    def recent_turns(self, n: int | None = None) -> list[Turn]:
        if n is None:
            return list(self.turns)
        return self.turns[-n:]

    def conversation_context(self, max_turns: int = 5) -> str:
        """Format the last N turns for prompt injection.

        Guarantees the result fits within both ``self.max_context_chars``
        and ``self.max_context_tokens`` (estimated as ``chars // 4``).
        Oldest turns are dropped first when either cap is exceeded.
        """
        if not self.turns:
            return ""
        char_cap = self.max_context_chars
        # 1 token ≈ 4 chars (conservative English estimate; avoids a tokenizer dep)
        tok_cap_chars = self.max_context_tokens * 4
        effective_cap = min(char_cap, tok_cap_chars)
        chunks = [t.summary() for t in self.recent_turns(max_turns)]
        text = "\n\n".join(chunks)
        if len(text) > effective_cap:
            while len(text) > effective_cap and len(chunks) > 1:
                chunks.pop(0)
                text = "\n\n".join(chunks)
            if len(text) > effective_cap:
                text = text[: effective_cap - 1].rstrip() + "…"
        return text

    # ---------- artifact cache ----------

    @staticmethod
    def cache_key(tool_name: str, arguments: dict[str, Any]) -> str:
        """Stable key for tool-output caching."""
        try:
            payload = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
        except Exception:
            payload = repr(sorted(arguments.items()))
        return f"{tool_name}::{payload}"

    def cache_lookup(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        return self.artifacts.get(self.cache_key(tool_name, arguments))

    def cache_store(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        output: Any,
        label: str,
    ) -> None:
        key = self.cache_key(tool_name, arguments)
        with self._cache_lock:
            self.artifacts[key] = {
                "tool": tool_name,
                "arguments": arguments,
                "output": output,
                "label": label,
                "turn_index": self._next_turn_index,  # belongs to the in-flight turn
                "stored_at": _now().isoformat(),
            }
            # Bounded artifact cache — evict oldest entries when over the cap.
            while len(self.artifacts) > self.max_artifacts:
                oldest_key = next(iter(self.artifacts))
                del self.artifacts[oldest_key]

    # ---------- inspection / clearing ----------

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turns": len(self.turns),
            "artifacts_cached": len(self.artifacts),
            "max_turns": self.max_turns,
            "max_context_chars": self.max_context_chars,
            "max_context_tokens": self.max_context_tokens,
            "max_artifacts": self.max_artifacts,
            "labels": [
                {"label": v["label"], "tool": v["tool"], "turn": v["turn_index"]}
                for v in self.artifacts.values()
            ],
        }

    def clear(self) -> None:
        self.turns.clear()
        self.artifacts.clear()
        self._next_turn_index = 1
