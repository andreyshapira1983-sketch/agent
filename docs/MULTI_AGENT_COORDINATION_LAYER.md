# Multi-Agent Coordination Layer

> Status: architectural proposal. This document describes a target capability that is not yet implemented. Where this document conflicts with the current code, the code is authoritative.

## 1. Purpose

The agent already has a central `AgentLoop`, bounded subagents, memory governance, tool restrictions, verification, budgets, and a subagent lifecycle specification. The missing layer is coordination between multiple agents after