"""FastAPI server exposing the agent as a bearer-token JSON API (uvicorn api.server:app)."""
from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

try:
    from fastapi import Depends, FastAPI, HTTPException, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel, Field
except ImportError as _e:
    raise ImportError(
        "FastAPI is required to run api/server.py. "
        "Install it with: pip install fastapi uvicorn"
    ) from _e


_API_TOKEN = os.getenv("AGENT_API_TOKEN", "").strip()
if not _API_TOKEN:
    raise RuntimeError(
        "AGENT_API_TOKEN environment variable must be set before starting the server. "
        "Generate a token with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

_MAX_QUESTION_CHARS = int(os.getenv("AGENT_API_MAX_QUESTION", "8000"))
_MAX_FILE_HINT_CHARS = int(os.getenv("AGENT_API_MAX_FILE_HINT", "512"))
_WORKSPACE = Path(os.getenv("AGENT_WORKSPACE", ".")).resolve()


def _build_server_agent():
    """Build an AgentLoop configured for server use (no interactive approvals)."""
    from app.bootstrap import build_agent  # lazy to avoid top-level side effects
    return build_agent(_WORKSPACE, with_memory=True, approval_provider=None)


_agent = None  # one shared agent, built on the first request

# Concurrent first requests must not build two agents.
_agent_lock = threading.Lock()

# FastAPI runs sync endpoints in a thread pool; the shared AgentLoop (one trace
# logger, working memory, usage ledger) must run one request at a time.
_run_lock = threading.Lock()


def _get_agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            # Double-checked: another thread may have built it while we waited.
            if _agent is None:
                _agent = _build_server_agent()
    return _agent


app = FastAPI(
    title="Agent API",
    description="HTTP interface for the autonomous agent loop.",
    version="1.0.0",
    docs_url="/docs",        # Swagger UI
    redoc_url="/redoc",
)

_bearer = HTTPBearer(auto_error=True)


def _require_auth(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    """Dependency: validate Bearer token using constant-time comparison."""
    if not secrets.compare_digest(credentials.credentials, _API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


class AskRequest(BaseModel):
    question: str = Field(
        ...,
        description="The user's natural-language question or task.",
        max_length=_MAX_QUESTION_CHARS,
    )
    file_hint: str | None = Field(
        default=None,
        description="Optional workspace-relative file path to pre-load.",
        max_length=_MAX_FILE_HINT_CHARS,
    )


class AskResponse(BaseModel):
    answer: str
    trace_id: str
    token_usage: dict[str, Any]


class HealthResponse(BaseModel):
    status: str


class UsageResponse(BaseModel):
    provider: str
    model: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness check — returns 200 OK when the server is running."""
    return HealthResponse(status="ok")


@app.get("/usage", response_model=UsageResponse, tags=["ops"],
         dependencies=[Depends(_require_auth)])
def usage() -> UsageResponse:
    """Cumulative LLM token usage since the server started."""
    agent = _get_agent()
    summary = agent.llm.usage_summary()
    return UsageResponse(**summary)


@app.post("/ask", response_model=AskResponse, tags=["agent"],
          dependencies=[Depends(_require_auth)])
def ask(body: AskRequest) -> AskResponse:
    """Run one agent cycle and return the answer."""
    agent = _get_agent()
    # The response read stays under the lock: trace_id and usage belong to this run.
    with _run_lock:
        try:
            answer = agent.run(
                user_question=body.question,
                file_hint=body.file_hint,
            )
        except Exception as exc:
            # A 500 without a stack trace in the body; the full error goes to the JSONL log.
            agent.log.log("api_error", {"error": type(exc).__name__, "message": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Agent error: {type(exc).__name__}",
            ) from exc

        return AskResponse(
            answer=answer,
            trace_id=agent.log.trace_id,
            token_usage=agent.llm.usage_summary(),
        )
