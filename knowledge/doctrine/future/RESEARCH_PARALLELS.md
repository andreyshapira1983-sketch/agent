# RESEARCH_PARALLELS

## Purpose
Study external literature on agent identity continuity across model replacement, and record evidence-backed parallels relevant to this agent's architecture and doctrine.

## Status
- **Draft** — initial entry, pending review.

## Evidence Status Labels
- **[verified]** — confirmed by a primary source or measured in this runtime.
- **[inferred]** — derived from doctrine or architecture, not directly measured.
- **[external]** — from external literature, not yet independently verified here.

## Key Parallels

### 1. Identity as Persistent State, Not Model Weights
- **[external]** — Literature on LLM agents (e.g., agent memory surveys) argues that identity continuity is preserved by persistent memory and state, not by the underlying model weights.
- **[inferred]** — This agent's architecture separates episodic memory, lessons, and doctrine from the model runtime, supporting the same principle.

### 2. Model Replacement as a Migration Event
- **[external]** — Research on model swapping in production agents treats replacement as a migration that must preserve state, logs, and learned lessons.
- **[inferred]** — The agent's self-repair and lesson-provenance mechanisms are designed to survive model changes by keeping receipts and doctrine in the workspace.

### 3. Governance as the Anchor of Continuity
- **[external]** — Multi-agent governance literature emphasises that central governance documents and contracts provide continuity when individual agents change.
- **[verified]** — This agent's CENTRAL_AGENT_GOVERNANCE.md and CORPORATE_MODEL.md serve that anchoring role.

## Open Questions
- How does the agent's identity persist when the planner or synthesizer model is replaced mid-session?
- Are there measured receipts proving that lessons and episodic memory survive a model swap?

## Next Steps
- Review this draft against the self-repair doctrine.
- Add measured evidence from read_logs or lesson_provenance where available.
- Expand with additional external literature once web access is available on this path.
