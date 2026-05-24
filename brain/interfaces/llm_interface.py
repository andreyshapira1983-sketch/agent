"""
brain/interfaces/llm_interface.py — LLM as a Tool

The LLM is an interface. The Brain calls it.
The Brain controls:
    - What prompt to send
    - What model to use
    - When to call it
    - Whether to trust the result

This file defines the abstract contract.
Concrete implementations live in adapters/ (e.g., OpenAIAdapter, AnthropicAdapter).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMRequest:
    """What the Brain sends to the LLM."""
    system_prompt: str
    context: dict
    max_tokens: int = 2048
    temperature: float = 0.2       # Low temp = more deterministic = Brain prefers this
    response_format: str = "json"  # Brain always requests structured output


@dataclass
class LLMResponse:
    """What comes back from the LLM — raw, not yet trusted."""
    raw_text: str
    parsed: dict        # Brain requires JSON — if parsing fails, Brain handles it
    tokens_used: int
    model: str


class LLMInterface(ABC):
    """
    Abstract interface for any LLM provider.

    The Brain depends on this interface, not on any specific LLM.
    Swap OpenAI for Anthropic → zero changes in Brain code.
    """

    @abstractmethod
    def call(self, context: dict) -> dict:
        """
        Called by the Brain with assembled context.
        Must return a dict that Interpreter can parse.

        The implementation must:
            1. Convert context → prompt
            2. Call the LLM API
            3. Parse JSON response
            4. Return parsed dict

        Must NEVER raise — return error dict instead.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Health check — can the Brain use this LLM right now?"""
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Which model this adapter uses — for logging."""
        raise NotImplementedError
