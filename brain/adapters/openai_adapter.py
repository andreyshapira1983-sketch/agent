"""
brain/adapters/openai_adapter.py — OpenAI LLM Adapter

Concrete implementation of LLMInterface for OpenAI API.
The Brain calls this — it knows nothing about OpenAI internals.

Keys are loaded from environment variables only.
Never hardcode credentials.
"""

from __future__ import annotations

import json
import logging
import os
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError

from ..interfaces.llm_interface import LLMInterface
from ..secrets import Secret, SecretsVault

logger = logging.getLogger(__name__)

# System prompt template — Brain controls what LLM knows about itself
SYSTEM_PROMPT = """
You are a reasoning engine. You do NOT have goals of your own.
You receive structured context from the Brain and return structured JSON.

You MUST always respond with valid JSON in this exact format:
{
    "action": "respond" | "tool_call" | "wait" | "clarify" | "stop",
    "content": <see rules below>,
    "confidence": <float 0.0–1.0>,
    "reasoning": <string explaining your decision>
}

CONTENT FORMAT RULES:
- If action="respond" or action="clarify": content must be a plain string (your answer/question)
- If action="tool_call": content MUST be a JSON object with EXACTLY this structure:
    {
        "tool_name": "<exact name of the tool to call>",
        "params": { "<param_name>": "<value>", ... }
    }
  NEVER set action="tool_call" with a string content or without specifying tool_name and params.
- If action="wait" or action="stop": content may be null or a brief string explanation

IMPORTANT FOR TOOL CALLS:
- Use only tool names listed in AVAILABLE TOOLS (if provided)
- Match parameter names exactly as shown in the tool description
- If you need multiple tool calls, return one tool_call at a time — you will be called again with the result
- Always prefer using a tool over guessing — the system will re-invoke you with the tool result

General rules:
- Never refuse to return JSON.
- If uncertain, set confidence below 0.6 and action to "clarify".
- Never add text outside the JSON block.
""".strip()


class OpenAIAdapter(LLMInterface):
    """
    Wraps OpenAI API for use as a Brain tool.
    Brain calls .call(context) → gets back a parsed dict.
    """

    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        *,
        api_key: Secret | None = None,
        vault: SecretsVault | None = None,
    ) -> None:
        """
        Args:
            api_key: Pre-loaded Secret. Preferred path — never a raw str.
            vault:   SecretsVault holding `OPENAI_API_KEY`. Convenience.
            model / max_tokens / temperature: unchanged.

        If neither `api_key` nor `vault` is provided, falls back to
        `os.environ["OPENAI_API_KEY"]` for backward compatibility.
        """
        resolved: Secret | None = api_key
        if resolved is None and vault is not None:
            resolved = vault.get_optional("OPENAI_API_KEY")
        if resolved is None:
            raw = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if not raw:
                raise EnvironmentError(
                    "OPENAI_API_KEY not found. "
                    "Pass api_key=Secret(...), or load it via SecretsVault, "
                    "or set OPENAI_API_KEY in your environment."
                )
            resolved = Secret(raw, name="OPENAI_API_KEY")

        # The reveal() below is the ONE audited call site where the raw
        # key crosses the boundary into the openai SDK. Do not log it,
        # do not store it on `self` as a string — keep the Secret wrapper.
        self._api_key: Secret = resolved
        self._client = OpenAI(api_key=self._api_key.reveal())
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self._max_tokens = max_tokens
        self._temperature = temperature
        logger.info("[OpenAIAdapter] Initialized | model=%s", self._model)

    # ------------------------------------------------------------------
    # LLMInterface implementation
    # ------------------------------------------------------------------

    def call(self, context: dict) -> dict:
        """
        Brain passes context → we build prompt → call OpenAI → return dict.
        Never raises — returns error dict on failure.

        Persona override:
            When `context["system_prompt"]` is provided, it is *prepended*
            to the structured-output rules instead of replacing them. This
            lets ChatHandler give the model a conversational persona while
            still keeping the JSON envelope the rest of the agent relies
            on.
        """
        user_message = self._build_user_message(context)
        system_message = _compose_system_prompt(context.get("system_prompt"))

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )

            raw_text = response.choices[0].message.content or ""
            tokens_used = response.usage.total_tokens if response.usage else 0

            logger.debug(
                "[OpenAIAdapter] Response received | tokens=%d model=%s",
                tokens_used,
                self._model,
            )

            return self._parse(raw_text)

        except RateLimitError:
            logger.warning("[OpenAIAdapter] Rate limit hit")
            return self._error_response("Rate limit exceeded — retry later")

        except APIConnectionError as exc:
            logger.error("[OpenAIAdapter] Connection error: %s", exc)
            return self._error_response(f"Connection error: {exc}")

        except APIStatusError as exc:
            logger.error("[OpenAIAdapter] API error %d: %s", exc.status_code, exc.message)
            return self._error_response(f"API error {exc.status_code}: {exc.message}")

        except (ValueError, KeyError, AttributeError) as exc:
            logger.error("[OpenAIAdapter] Unexpected error: %s", exc)
            return self._error_response(f"Unexpected error: {exc}")

    def is_available(self) -> bool:
        """Quick health check — verify we have a non-empty API key."""
        return bool(self._api_key)

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_user_message(self, context: dict) -> str:
        """
        Convert Brain context dict → plain text prompt for OpenAI.
        Brain controls what's in context — we just serialize it.
        """
        parts = []

        if context.get("goals"):
            goals_text = "\n".join(
                f"  - [{g['priority']}] {g['text']}" for g in context["goals"]
            )
            parts.append(f"CURRENT GOALS:\n{goals_text}")

        if context.get("history"):
            history_text = "\n".join(
                f"  {m.get('role', 'unknown')}: {m.get('content', '')}"
                for m in context["history"][-5:]  # last 5 messages only
            )
            parts.append(f"RECENT HISTORY:\n{history_text}")

        # S1.3: inject semantically similar past episodes
        if context.get("similar_episodes"):
            ep_lines = []
            for ep in context["similar_episodes"][:3]:
                score = ep.get("score", 0.0)
                text = ep.get("text", "")[:200]
                ep_lines.append(f"  [{score:.2f}] {text}")
            parts.append("SIMILAR PAST EPISODES (for reference):\n" + "\n".join(ep_lines))

        if context.get("facts"):
            facts_text = "\n".join(
                f"  - {f.get('text', '')}" for f in context["facts"]
            )
            parts.append(f"RELEVANT FACTS:\n{facts_text}")

        # Include tool descriptions when provided by the caller
        if context.get("tools"):
            tool_lines = []
            for t in context["tools"]:
                name = t.get("name", "?")
                desc = t.get("description", "")
                params = t.get("parameters", {})
                param_str = "  ".join(f"{k}: {v}" for k, v in params.items())
                tool_lines.append(f"  • {name}: {desc}\n    parameters: {param_str or 'none'}")
            tools_block = "\n".join(tool_lines)
            parts.append(
                f"AVAILABLE TOOLS (use these for tool_call):\n{tools_block}\n\n"
                "When calling a tool, set action=\"tool_call\" and content={\"tool_name\": \"<name>\", \"params\": {...}}"
            )

        parts.append(f"USER INPUT:\n  {context.get('input', '')}")

        return "\n\n".join(parts)

    def _parse(self, raw_text: str) -> dict:
        """Parse JSON response — return error dict if invalid."""
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning("[OpenAIAdapter] JSON parse failed: %s", exc)
            return self._error_response(f"JSON parse error: {exc}")

    @staticmethod
    def _error_response(reason: str) -> dict:
        return {
            "action": "wait",
            "content": None,
            "confidence": 0.0,
            "reasoning": reason,
        }


# ────────────────────────────────────────────────────────────────────
# Persona composition
# ────────────────────────────────────────────────────────────────────

# The structured-output rules every adapter response must follow.
# Personas may prepend a tone/voice section but never replace these.
_JSON_ENVELOPE_RULES = """

OUTPUT FORMAT — non-negotiable:

You MUST always respond with valid JSON in this exact format:
{
    "action": "respond" | "tool_call" | "wait" | "clarify" | "stop",
    "content": <see rules below>,
    "confidence": <float 0.0-1.0>,
    "reasoning": <string explaining your decision>
}

CONTENT FORMAT RULES:
- If action="respond" or action="clarify": content must be a plain string
  (your answer/question in natural language).
- If action="tool_call": content MUST be a JSON object with EXACTLY this
  structure: {"tool_name": "<exact name>", "params": {...}}
- If action="wait" or action="stop": content may be null or a brief string.

IMPORTANT FOR TOOL CALLS:
- Use only tool names listed in AVAILABLE TOOLS.
- Match parameter names exactly as shown.
- If you need multiple tool calls, return one at a time.

General rules:
- Never refuse to return JSON.
- If uncertain, set confidence below 0.6 and action="clarify".
- Never add text outside the JSON block.
""".strip()


def _compose_system_prompt(persona: str | None) -> str:
    """Build the system message — persona (optional) + JSON envelope rules."""
    if persona and persona.strip():
        return f"{persona.strip()}\n\n{_JSON_ENVELOPE_RULES}"
    return SYSTEM_PROMPT
