# Modular Autonomous Agent

This repository now treats the modular `brain/runtime/tools` architecture as the source of truth.

The older 46-layer experiment is preserved locally under `_archive/46-layer-experiment/` and should be treated as roadmap/reference material, not as the runtime entrypoint.

## Current Architecture

```text
channels/        external intake: Telegram, email
runtime/         wiring, config, live loop, senders, chat handler
brain/           cognitive core, context, memory, policy, planner, audit, skills
tools/           passive tool registry, executor, handler, built-in tools
professions/     YAML workflows for job-style capabilities
tests/           focused tests for brain, runtime, tools, channels
```

Core flow:

```text
Input -> Runtime/Channel -> Brain.think -> Policy -> Response or ToolCall -> ToolExecutor -> Memory/Audit/Feedback
```

## Why This Shape

- `Brain` decides when to use the LLM. The LLM is a tool, not the controller.
- `PolicyEngine` is the safety gate for tool calls and consequential actions.
- `ToolExecutor` is the only layer that actually runs tools.
- `runtime/agent_runtime.py` wires the system together and is the composition root.
- 46-layer ideas are kept as roadmap material until they become small, tested modules.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python run_live.py
```

For tests:

```powershell
python -m pytest
```

## Source Of Truth

Use these files first when navigating the project:

- `brain/core.py` — reasoning cycle and job intake
- `runtime/agent_runtime.py` — subsystem wiring
- `tools/executor.py` — tool execution boundary
- `tools/handler.py` — Brain tool_call adapter
- `brain/policy.py` — approval/deny policy
- `brain/privacy.py` — PII redaction
- `brain/skills/workflow_runner.py` — profession workflow execution

## Roadmap Rule

New autonomous-agent ideas should start in documentation or a small isolated module. They should only enter the runtime path when they have:

1. A clear owner module.
2. A typed input/output contract.
3. A policy/safety story.
4. A focused test.
