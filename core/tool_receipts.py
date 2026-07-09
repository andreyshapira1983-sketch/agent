"""Append-only tool receipt ledger — Stage 1 evidence layer (slice 1a + G5b).

Records bounded tool observations for filesystem / shell / test / network tools
(slice 1a) and successful ``file_write`` executions (Gateway G5b).
Receipts are redacted before persist; no raw secrets in ``data/tool_receipts.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from core.ids import new_id
from core.models import ToolCall, ToolResult
from core.redaction import redact_payload
from core.state_integrity import append_state_jsonl, read_state_jsonl

DEFAULT_TOOL_RECEIPTS_PATH = "data/tool_receipts.jsonl"

ReceiptPath = Literal["repl", "runtime", "daemon", "unknown"]
ReceiptKind = Literal["tool", "approval", "gateway"]
ReceiptStatus = Literal["success", "error", "blocked", "skipped"]

# Slice 1a — filesystem / shell / test / network only (see docs/proposals/tool-receipts-proposal.md).
SLICE_1A_RECEIPT_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "list_dir",
        "read_logs",
        "diff_file",
        "shell_exec",
        "run_tests",
        "web_fetch",
        "web_search",
        "rss_fetch",
        "semantic_scholar_search",
    }
)

# Gateway G5b — effect-tool execution proof (paired with gateway.allow; no raw file body).
SLICE_G5B_EFFECT_RECEIPT_TOOLS: frozenset[str] = frozenset({"file_write"})

_RECEIPT_TOOLS: frozenset[str] = SLICE_1A_RECEIPT_TOOLS | SLICE_G5B_EFFECT_RECEIPT_TOOLS


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReceiptContext:
    trace_id: str = ""
    path: ReceiptPath = "unknown"
    workspace: Path | None = None


_receipt_context: ContextVar[ReceiptContext | None] = ContextVar(
    "tool_receipt_context", default=None
)


def get_receipt_context() -> ReceiptContext | None:
    return _receipt_context.get()


def bind_receipt_context(
    *,
    trace_id: str = "",
    path: ReceiptPath = "unknown",
    workspace: Path | str | None = None,
) -> Token[ReceiptContext | None]:
    ws = Path(workspace).resolve() if workspace is not None else None
    return _receipt_context.set(
        ReceiptContext(trace_id=trace_id, path=path, workspace=ws)
    )


def reset_receipt_context(token: Token[ReceiptContext | None]) -> None:
    _receipt_context.reset(token)


@contextmanager
def receipt_context(
    *,
    trace_id: str = "",
    path: ReceiptPath = "unknown",
    workspace: Path | str | None = None,
) -> Iterator[None]:
    token = bind_receipt_context(trace_id=trace_id, path=path, workspace=workspace)
    try:
        yield
    finally:
        reset_receipt_context(token)


def default_receipts_path(workspace: Path | str) -> Path:
    return Path(workspace) / DEFAULT_TOOL_RECEIPTS_PATH


def _resolve_workspace(ctx: ReceiptContext | None) -> Path | None:
    if ctx is not None and ctx.workspace is not None:
        return ctx.workspace
    raw = os.getenv("AGENT_WORKSPACE", "").strip()
    if raw:
        return Path(raw).resolve()
    return None


def _fingerprint(value: Any) -> str:
    redacted = redact_payload(value)
    blob = json.dumps(redacted, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _args_fingerprint(arguments: dict[str, Any]) -> str:
    return _fingerprint(arguments)


def _result_fingerprint(result: ToolResult) -> str:
    if result.status == "error":
        return _fingerprint(
            {"status": "error", "error": result.error, "latency_ms": result.latency_ms}
        )
    output = result.output
    if isinstance(output, str):
        preview = output[:512] if len(output) > 512 else output
        return _fingerprint(
            {
                "status": result.status,
                "output_len": len(output),
                "output_preview": preview,
                "latency_ms": result.latency_ms,
            }
        )
    return _fingerprint(
        {
            "status": result.status,
            "output_type": type(output).__name__,
            "output": output,
            "latency_ms": result.latency_ms,
        }
    )


def _build_summary(tool_name: str, arguments: dict[str, Any], result: ToolResult) -> str:
    parts = [f"{tool_name} {result.status}"]
    for key in ("path", "command", "url", "query", "pattern"):
        if key in arguments and arguments[key]:
            parts.append(f"{key}={arguments[key]}")
    summary = " ".join(parts)
    redacted = redact_payload(summary)
    return redacted if isinstance(redacted, str) else str(redacted)


def _file_write_args_fingerprint(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    content = arguments.get("content")
    content_len = len(content.encode("utf-8")) if isinstance(content, str) else 0
    return _fingerprint({"path": path, "content_byte_len": content_len})


def _file_write_result_fingerprint(result: ToolResult) -> str:
    output = result.output
    if not isinstance(output, dict):
        return _fingerprint({"status": result.status, "output_type": type(output).__name__})
    return _fingerprint(
        {
            "status": result.status,
            "path": output.get("path"),
            "mode": output.get("mode"),
            "bytes_written": output.get("bytes_written"),
            "backup_path": output.get("backup_path"),
            "latency_ms": result.latency_ms,
        }
    )


def _build_file_write_summary(arguments: dict[str, Any], result: ToolResult) -> str:
    path = arguments.get("path", "")
    content = arguments.get("content")
    content_len = len(content.encode("utf-8")) if isinstance(content, str) else 0
    mode = ""
    bytes_written = content_len
    if isinstance(result.output, dict):
        mode = str(result.output.get("mode") or "")
        if isinstance(result.output.get("bytes_written"), int):
            bytes_written = int(result.output["bytes_written"])
    parts = [f"file_write {result.status}", f"path={path}", f"bytes={bytes_written}"]
    if mode:
        parts.append(f"mode={mode}")
    summary = " ".join(parts)
    redacted = redact_payload(summary)
    return redacted if isinstance(redacted, str) else str(redacted)


@dataclass(frozen=True)
class ToolReceipt:
    receipt_id: str
    operation: str
    status: ReceiptStatus
    summary: str
    args_fingerprint: str
    result_fingerprint: str
    risk: str = "read_only"
    kind: ReceiptKind = "tool"
    trace_id: str = ""
    path: ReceiptPath = "unknown"
    refs: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "trace_id": self.trace_id,
            "path": self.path,
            "kind": self.kind,
            "operation": self.operation,
            "risk": self.risk,
            "status": self.status,
            "summary": self.summary,
            "args_fingerprint": self.args_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "refs": dict(self.refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolReceipt":
        refs = data.get("refs") if isinstance(data.get("refs"), dict) else {}
        return cls(
            receipt_id=str(data.get("receipt_id") or new_id("trc")),
            trace_id=str(data.get("trace_id") or ""),
            path=str(data.get("path") or "unknown"),  # type: ignore[arg-type]
            kind=str(data.get("kind") or "tool"),  # type: ignore[arg-type]
            operation=str(data.get("operation") or ""),
            risk=str(data.get("risk") or "read_only"),
            status=str(data.get("status") or "success"),  # type: ignore[arg-type]
            summary=str(data.get("summary") or ""),
            args_fingerprint=str(data.get("args_fingerprint") or ""),
            result_fingerprint=str(data.get("result_fingerprint") or ""),
            refs={str(k): str(v) for k, v in refs.items()},
            created_at=str(data.get("created_at") or _utc_now_iso()),
        )


@dataclass
class ToolReceiptLedger:
    path: Path

    def append_receipt(self, receipt: ToolReceipt) -> ToolReceipt:
        payload = redact_payload(receipt.to_dict())
        if not isinstance(payload, dict):
            raise ValueError("receipt payload must be a dict after redaction")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_state_jsonl(self.path, [payload])
        return ToolReceipt.from_dict(payload)

    def load(self) -> list[ToolReceipt]:
        if not self.path.exists():
            return []
        out: list[ToolReceipt] = []
        for row in read_state_jsonl(self.path):
            try:
                out.append(ToolReceipt.from_dict(row))
            except (TypeError, ValueError):
                continue
        return out

    def get(self, receipt_id: str) -> ToolReceipt | None:
        for receipt in self.load():
            if receipt.receipt_id == receipt_id:
                return receipt
        return None

    def list_by_operation(self, operation: str) -> list[ToolReceipt]:
        return [r for r in self.load() if r.operation == operation]

    def list_by_trace(self, trace_id: str) -> list[ToolReceipt]:
        return [r for r in self.load() if r.trace_id == trace_id]


def append_receipt(
    receipt: ToolReceipt,
    *,
    workspace: Path | str | None = None,
) -> ToolReceipt | None:
    """Append one receipt to the workspace ledger. Returns None if no workspace."""
    ws = Path(workspace).resolve() if workspace is not None else _resolve_workspace(
        get_receipt_context()
    )
    if ws is None:
        return None
    ledger = ToolReceiptLedger(default_receipts_path(ws))
    return ledger.append_receipt(receipt)


def record_tool_invoke_receipt(
    tool: Any,
    call: ToolCall,
    result: ToolResult,
) -> ToolReceipt | None:
    """Record a tool receipt when context/workspace is available.

    Slice 1a tools always emit success/error rows. G5b ``file_write`` emits on
    successful execution only — permission proof stays on gateway receipts.
    """
    tool_name = getattr(tool, "name", call.tool_name)
    if tool_name not in _RECEIPT_TOOLS:
        return None
    if tool_name in SLICE_G5B_EFFECT_RECEIPT_TOOLS and result.status != "success":
        return None

    ctx = get_receipt_context()
    ws = _resolve_workspace(ctx)
    if ws is None:
        return None

    risk = "read_only"
    risk_for = getattr(tool, "risk_for", None)
    if callable(risk_for):
        try:
            risk = str(risk_for(call.arguments))
        except Exception:
            risk = str(getattr(tool, "risk", "read_only"))

    status: ReceiptStatus = "success" if result.status == "success" else "error"
    if tool_name == "file_write":
        summary = _build_file_write_summary(call.arguments, result)
        args_fp = _file_write_args_fingerprint(call.arguments)
        result_fp = _file_write_result_fingerprint(result)
    else:
        summary = _build_summary(tool_name, call.arguments, result)
        args_fp = _args_fingerprint(call.arguments)
        result_fp = _result_fingerprint(result)

    receipt = ToolReceipt(
        receipt_id=new_id("trc"),
        trace_id=(ctx.trace_id if ctx else ""),
        path=(ctx.path if ctx else "unknown"),
        operation=tool_name,
        risk=risk,
        status=status,
        summary=summary,
        args_fingerprint=args_fp,
        result_fingerprint=result_fp,
        refs={"tool_call_id": call.id},
    )
    return append_receipt(receipt, workspace=ws)


def _build_approval_summary(operation: str, item: Any) -> str:
    parts = [operation]
    op = str(getattr(item, "operation", "") or "")
    risk = str(getattr(item, "risk", "") or "")
    if op:
        parts.append(f"op={op}")
    if risk:
        parts.append(f"risk={risk}")
    summary = " ".join(parts)
    redacted = redact_payload(summary)
    return redacted if isinstance(redacted, str) else str(redacted)


def record_approval_receipt(
    operation: str,
    item: Any,
    *,
    status: ReceiptStatus = "success",
    workspace: Path | str | None = None,
) -> ToolReceipt | None:
    """Record an approval-inbox transition receipt (slice 1b-a).

    ``operation`` is an explicit ``approval_inbox.*`` name. ``item`` is the
    ApprovalInboxItem after the transition. Only fingerprints + a redacted
    summary are stored — never raw payload/secrets. Returns None when no
    workspace can be resolved (e.g. in-memory inbox with no receipt context).
    """
    ctx = get_receipt_context()
    ws = (
        Path(workspace).resolve()
        if workspace is not None
        else _resolve_workspace(ctx)
    )
    if ws is None:
        return None

    item_id = str(getattr(item, "id", "") or "")
    item_operation = str(getattr(item, "operation", "") or "")
    risk = str(getattr(item, "risk", "reversible") or "reversible")
    item_status = str(getattr(item, "status", "") or "")
    receipt = ToolReceipt(
        receipt_id=new_id("trc"),
        trace_id=(ctx.trace_id if ctx else ""),
        path=(ctx.path if ctx else "unknown"),
        kind="approval",
        operation=operation,
        risk=risk,
        status=status,
        summary=_build_approval_summary(operation, item),
        args_fingerprint=_fingerprint(
            {"approval_id": item_id, "operation": item_operation, "risk": risk}
        ),
        result_fingerprint=_fingerprint(
            {"status": item_status, "updated_at": str(getattr(item, "updated_at", ""))}
        ),
        refs={"approval_id": item_id},
    )
    try:
        return append_receipt(receipt, workspace=ws)
    except Exception:
        return None  # documented contract: never raises


def _build_gateway_summary(decision: Any) -> str:
    outcome = str(getattr(decision, "outcome", "") or "")
    tool_name = str(getattr(decision, "tool_name", "") or "")
    path = str(getattr(decision, "path", "") or "")
    parts = [f"gateway.{outcome}"]
    if tool_name:
        parts.append(f"tool={tool_name}")
    if path:
        parts.append(f"path={path}")
    summary = " ".join(parts)
    redacted = redact_payload(summary)
    return redacted if isinstance(redacted, str) else str(redacted)


def record_gateway_receipt(
    decision: Any,
    *,
    workspace: Path | str | None = None,
    status: ReceiptStatus = "success",
    refs: dict[str, str] | None = None,
) -> ToolReceipt | None:
    """Record a gateway *decision* receipt (slice G4).

    ``decision`` is an ``ActuationGateway`` GatewayDecision (or a stand-in with
    ``outcome`` / ``tool_name`` / ``path`` / optional ``policy`` / ``reasons``).
    Only fingerprints + a redacted summary are stored — never raw tool args,
    proposal payloads, or file/patch contents. Mirrors ``record_approval_receipt``:
    append-only, never raises, returns None when no workspace can be resolved.
    """
    ctx = get_receipt_context()
    ws = (
        Path(workspace).resolve()
        if workspace is not None
        else _resolve_workspace(ctx)
    )
    if ws is None:
        return None

    outcome = str(getattr(decision, "outcome", "") or "unknown")
    tool_name = str(getattr(decision, "tool_name", "") or "")
    gateway_path = str(getattr(decision, "path", "") or "")
    policy = getattr(decision, "policy", None)
    policy_decision = str(getattr(policy, "decision", "") or "") if policy is not None else ""
    reasons = list(getattr(decision, "reasons", ()) or ())

    risk = str(getattr(policy, "risk", "") or "") or "gateway"

    merged_refs: dict[str, str] = {}
    if tool_name:
        merged_refs["tool_name"] = tool_name
    if gateway_path:
        merged_refs["gateway_path"] = gateway_path
    if refs:
        for key, value in refs.items():
            if value is not None:
                merged_refs[str(key)] = str(value)

    receipt = ToolReceipt(
        receipt_id=new_id("trc"),
        trace_id=(ctx.trace_id if ctx else ""),
        path=(ctx.path if ctx else "unknown"),
        kind="gateway",
        operation=f"gateway.{outcome}",
        risk=risk,
        status=status,
        summary=_build_gateway_summary(decision),
        args_fingerprint=_fingerprint(
            {
                "outcome": outcome,
                "tool_name": tool_name,
                "gateway_path": gateway_path,
                "policy_decision": policy_decision,
            }
        ),
        result_fingerprint=_fingerprint({"outcome": outcome, "reasons": reasons}),
        refs=merged_refs,
    )
    try:
        return append_receipt(receipt, workspace=ws)
    except Exception:
        return None  # documented contract: never raises
