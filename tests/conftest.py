"""Shared test fixtures and helpers."""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from core.model_router import _DEFAULT_PROVIDER_ENV, _provider_has_credentials
from core.planner import PlannerOutput

# ── MIR-053: the suite denies outbound network by default ────────────────────
#
# Before this boundary existed, correctness depended on every author
# remembering the per-file convention (delete keys, set mock routing): one
# diagnostic run forgot, picked live keys up from `.env`, and billed a real
# provider request — silently. The deny turns that convention into a default:
# an outbound connect raises loudly with this marker, loopback stays open
# (local test servers are legitimate), and a run that genuinely needs the
# network must say so with ``AGENT_TESTS_ALLOW_NETWORK=1``.
#
# Scope, deliberately: `connect`/`connect_ex` on ANY Python-level socket —
# that includes TCP (the layer every HTTP client in the suite must pass
# through) and a UDP socket that uses `connect`; the guard does not inspect
# the socket type, and it does not need to. DNS resolution is left alone
# (the OS resolver does not go through `socket.connect`, and a lookup neither
# bills nor mutates). Scripts run OUTSIDE pytest are not covered here — a
# conftest only exists inside the suite; that half of MIR-053 stays open in
# the registry.

_NETWORK_DENY_MARKER = (
    "MIR-053: outbound network from tests is denied by default "
    "(target {address!r}). Loopback is allowed; a test that genuinely needs "
    "the network must run with AGENT_TESTS_ALLOW_NETWORK=1."
)

_LOOPBACK_HOSTS = frozenset({"localhost", "::1", ""})


def _connect_target_is_local(address: object) -> bool:
    """True for targets the deny leaves open.

    Non-tuple addresses (AF_UNIX paths) never leave the machine. Inside a
    tuple the host may legally be ``str`` or ``bytes`` — the socket module
    accepts both, so bytes are decoded rather than waved through (a bytes
    host used to fail OPEN, which was a bypass); any other host type fails
    CLOSED. Loopback is recognised by `ipaddress.is_loopback` when the host
    parses as an IP literal (covers 127.0.0.0/8, ``::1`` and the IPv4-mapped
    form), by name for "localhost"/"", and by the ``127.`` prefix for
    shorthand literals like ``127.1`` that `ipaddress` rejects but the OS
    resolver reads as 127.0.0.1.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False        # undecodable target — deny, never guess
    if not isinstance(host, str):
        return False            # unknown host shape inside a tuple — deny
    host = host.strip("[]").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.removeprefix("::ffff:")).is_loopback
    except ValueError:
        pass
    return host.startswith("127.")


@pytest.fixture(scope="session", autouse=True)
def _deny_outbound_network():
    if os.environ.get("AGENT_TESTS_ALLOW_NETWORK") == "1":
        yield
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        if _connect_target_is_local(address):
            return real_connect(self, address)
        raise RuntimeError(_NETWORK_DENY_MARKER.format(address=address))

    def guarded_connect_ex(self, address):
        # `connect_ex` swallows OSError by design, so it needs its own guard:
        # a client probing with it would otherwise dial out and read the code.
        if _connect_target_is_local(address):
            return real_connect_ex(self, address)
        raise RuntimeError(_NETWORK_DENY_MARKER.format(address=address))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", guarded_connect)
        mp.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        yield


class FakeLLM:
    """A drop-in LLM stand-in.

    Records every `complete(...)` call and returns canned responses
    from `responses` in order. Falls back to an empty JSON object so
    a forgotten queue doesn't crash the planner.
    """

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        # Loop builds Action with side_effects="read" if tool_name is set,
        # which doesn't depend on .provider, but other code paths may peek.
        self.provider = "fake"
        self.model = "fake-1"

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class FakePlanner:
    """A Planner stand-in that emits whatever sources the test gives it.

    Bypasses `core.step_sanitizer.sanitize_step`, which is exactly what we want when
    we need to verify the loop's defenses (policy gate, registry lookup) on
    plans the real planner would never produce.
    """

    def __init__(self, sources: list[dict[str, Any]] | None = None, reasoning: str = "fake-plan"):
        self.sources: list[dict[str, Any]] = list(sources or [])
        self.reasoning = reasoning
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
        llm=None,
    ) -> PlannerOutput:
        self.calls.append(
            {
                "question": question,
                "file_hint": file_hint,
                "history": history,
                "failure_context": failure_context,
                "forbidden_actions": forbidden_actions,
            }
        )
        # MVP-12: if the loop forbade a (tool, args) pair, the real
        # planner would drop it. FakePlanner emulates that by filtering
        # `self.sources` against the forbidden set on EACH call.
        import json as _json

        forbidden_set = set(forbidden_actions)
        sources_out: list[dict[str, Any]] = []
        for src in self.sources:
            tool = src.get("tool")
            args = src.get("arguments") or {}
            try:
                canonical = _json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical = ""
            if isinstance(tool, str) and canonical and (tool, canonical) in forbidden_set:
                continue
            sources_out.append(src)
        return PlannerOutput(
            reasoning=self.reasoning,
            sources=sources_out,
            raw_response="",
            warnings=[],
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


# Operator-facing model-routing env vars. These are normally set by the
# daemon's .env (e.g. AGENT_MODEL_POLICY=balanced) or by an operator running
# the suite offline (AGENT_PROVIDER=mock / AGENT_MODEL_POLICY=offline /
# AGENT_ALLOW_MOCK_ROUTING=1). Tests that exercise routing assert a specific
# policy and must NOT silently inherit whatever the ambient environment carries
# — otherwise the suite is green only by accident of the shell. The daemon's own
# health-check runs this suite under its .env, so hermeticity here keeps that
# self-test stable regardless of routing mode.
#: Семейства переменных, которыми оператор настраивает выбор модели. Держим
#: префиксами, а не поимённо: список имён дрейфует молча — переменную
#: добавляют в роутер, а про тесты забывают. Так и вышло с
#: `AGENT_TIER_PROVIDERS_STANDARD`: она осталась вне списка, протекла из .env
#: оператора и роняла тест роутинга у него, оставаясь зелёным в CI.
_OPERATOR_MODEL_ENV_PREFIXES = ("AGENT_TIER_PROVIDERS_", "AGENT_MODEL_TIER_")

_OPERATOR_MODEL_ENV_VARS = (
    "AGENT_MODEL_POLICY",
    "AGENT_MODEL_MAX_COST",
    "AGENT_ALLOW_MOCK_ROUTING",
    "AGENT_PROVIDER",
    "AGENT_MODEL",
    "AGENT_MODEL_REGISTRY_JSON",
    "AGENT_MODEL_REGISTRY_PATH",
    "AGENT_TIER_PROVIDERS_LIGHT",
    "AGENT_TIER_PROVIDERS_STANDARD",
    "AGENT_TIER_PROVIDERS_DEEP",
    "AGENT_MODEL_TIER_LIGHT",
    "AGENT_MODEL_TIER_STANDARD",
    "AGENT_MODEL_TIER_DEEP",
)


@pytest.fixture(autouse=True)
def _neutralize_operator_model_env(monkeypatch, tmp_path_factory):
    """Strip ambient model-routing overrides before each test.

    Runs before the test body, so a test that explicitly sets one of these
    (via its own monkeypatch.setenv) still wins. In a plain `pytest` run these
    vars are usually unset, making this a no-op; under an offline/mock operator
    environment it prevents routing tests from flipping red.
    """
    ambient = [k for k in os.environ if k.startswith(_OPERATOR_MODEL_ENV_PREFIXES)]
    for var in (*_OPERATOR_MODEL_ENV_VARS, *ambient):
        monkeypatch.delenv(var, raising=False)
    empty_registry = tmp_path_factory.getbasetemp() / "empty_model_registry.json"
    empty_registry.write_text('{"models": []}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_PATH", str(empty_registry))


# Deliberately not a key shape any provider would accept: it must satisfy a
# presence check and fail loudly if it ever reached a real client.
_PLACEHOLDER_CREDENTIAL = "placeholder-not-a-real-key"


def _real_credential_present() -> bool:
    """True when the machine already has credentials for some real provider.

    Deliberately asks `core.model_router` instead of re-listing env vars here.
    Duplicating the list drifts: `local` needs *both* `LOCAL_LLM_BASE_URL` and
    `LOCAL_LLM_MODEL`, and every check ignores whitespace-only values, so a
    hand-rolled `any(os.environ.get(...))` would call a half-configured machine
    "credentialed" and then leave it without a usable one.

    `mock` is excluded because it requires nothing, so it would answer True
    unconditionally and suppress the placeholder everywhere.
    """
    return any(
        _provider_has_credentials(provider)
        for provider, required in _DEFAULT_PROVIDER_ENV.items()
        if required
    )


@pytest.fixture(autouse=True)
def _ensure_placeholder_credential(monkeypatch):
    """Guarantee the suite sees *a* credential without needing a real one.

    `AgentLoop` is only buildable when `model_router._provider_has_credentials`
    finds a provider's env vars set, so on a machine with no credentials at all a
    handful of tests fail for an environmental reason rather than a real defect.
    Those tests never talk to a provider — no test constructs a real client — so
    the credential is read purely to answer "is one present?". A fake value
    answers that question exactly as well as a real one.

    This closes the gap the CI comment (PR #178) left open: CI supplied real
    secrets to reach an operator-like state, which a local clone cannot do. A
    placeholder reaches the same state offline, so `pytest` is green on a fresh
    checkout with nothing configured.

    Deliberately narrow:

    * It defers to reality. If any provider is fully credentialed the fixture
      does nothing, so CI keeps running under its real secrets and an operator's
      own keys still drive routing.
    * It cannot leak. The value is a constant in this file, carries no secret
      and is never written anywhere.
    * It cannot shadow the daemon. A fixture only exists inside `pytest`; the
      runtime entry points (`cli/app.py`, `agent_tick.py`, `api/server.py`) load
      `.env` in a separate process and are untouched.
    * It stays out of the way. Running before the test body means a test that
      manages credentials itself — several delete them to force offline routing
      — still wins.
    """
    if _real_credential_present():
        return
    monkeypatch.setenv("ANTHROPIC_API_KEY", _PLACEHOLDER_CREDENTIAL)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An isolated workspace directory."""
    return tmp_path


def run_git(root: Path, *args: str) -> None:
    """Одна команда git в ``root``. Argv из литералов, без оболочки."""
    import subprocess  # nosec B404

    subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "-c", "user.name=T", "-c", "user.email=t@localhost", *args],  # noqa: S607
        cwd=str(root), check=True, capture_output=True, text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Пустой git-репозиторий с одним коммитом на ветке main.

    Живёт здесь, а не в файле теста: локальная фикстура затеняет собственное
    имя в каждом тесте-потребителе, и pylint внутри Codacy справедливо зовёт
    это `redefined-outer-name` (8 замечаний на PR #301). Общая фикстура из
    conftest этой тени не создаёт.
    """
    run_git(tmp_path, "init")
    run_git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/main")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-m", "init")
    return tmp_path


def write_legacy_episode(path: Path, episode) -> None:
    """Append an episode row the way it looked BEFORE `usage_eligible` existed.

    A legacy row is an artefact of an older schema, so a test needs to write one
    the way that schema wrote it: with the key simply absent. Seeding it through
    `EpisodicMemoryStore.save(...)` no longer works and must not — `save` is the
    admission boundary, and an episode that reaches it always leaves with an
    explicit verdict. Manufacturing "never classified" through the door that
    exists to classify is what the boundary closes.
    """
    from core.state_integrity import (
        append_state_jsonl_unlocked,
        read_state_jsonl_unlocked,
        rewrite_state_jsonl_unlocked,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    row = episode.to_dict()
    row.pop("usage_eligible", None)
    if path.exists():
        rows = read_state_jsonl_unlocked(path)
        rows.append(row)
        rewrite_state_jsonl_unlocked(path, rows)
    else:
        append_state_jsonl_unlocked(path, [row])
