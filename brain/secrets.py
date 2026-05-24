"""
brain/secrets.py — Centralized secrets management.

Why this module exists:
    Raw API keys must never appear as plain `str` in the codebase.
    A plain string can be silently logged, repr'd, added to an f-string,
    pasted into an exception, or sent to the LLM as part of context.

    `Secret` is an opaque wrapper:
        - str(secret)  → '***'
        - repr(secret) → 'Secret(NAME=***)'
        - .reveal()    → real value (single, audited call site per consumer)

    `SecretsVault` is the single point of access. Adapters and tools should
    take a Secret (or a Vault + name) — never a bare `str`.

Loading priority (caller decides which to use):
    1. load_from_dpapi(path) — encrypted blob, Windows only (recommended for prod)
    2. load_dotenv(path)     — .env file (development)
    3. load_from_env(*names) — process environment variables

Migration:
    Once you've encrypted your .env via `encrypt_dotenv_to_dpapi()`,
    delete the plaintext .env and load via DPAPI. The encrypted blob can
    only be decrypted by the same Windows user account — a stolen laptop
    with a different login cannot read it.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────

class SecretNotFoundError(KeyError):
    """Raised when a requested secret has not been loaded into the vault."""


# ────────────────────────────────────────────────────────────────────
# Secret wrapper
# ────────────────────────────────────────────────────────────────────

class Secret:
    """
    Opaque wrapper around a sensitive string.

    Designed so that accidental logging, f-string interpolation, or
    exception messages cannot leak the underlying value.

    Only `.reveal()` returns the raw string — and that call site is the
    *only* place where the secret enters the rest of the program.
    """

    __slots__ = ("_value", "_name")

    def __init__(self, value: str, name: str = "") -> None:
        if not isinstance(value, str):
            raise TypeError(f"Secret value must be str, got {type(value).__name__}")
        self._value = value
        self._name = name

    # ---- safe accessors ----------------------------------------------------

    def reveal(self) -> str:
        """Return the real secret value. Use sparingly and audit call sites."""
        return self._value

    @property
    def name(self) -> str:
        return self._name

    # ---- safe representations ---------------------------------------------

    def __repr__(self) -> str:
        return f"Secret({self._name or '?'}=***)"

    def __str__(self) -> str:
        return "***"

    def __format__(self, _spec: str) -> str:
        # Ignore format spec — secrets never expand into f-strings
        return "***"

    # ---- behaviour ---------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)


# ────────────────────────────────────────────────────────────────────
# Vault
# ────────────────────────────────────────────────────────────────────

class SecretsVault:
    """
    Single point of access for all secrets in the agent runtime.

    Typical bootstrap:

        vault = SecretsVault()
        # Development: load from .env
        vault.load_dotenv(Path(".env"))
        # Production on Windows: load from encrypted blob
        # vault.load_from_dpapi(Path("config/secrets.dpapi"))

        adapter = OpenAIAdapter(api_key=vault.get("OPENAI_API_KEY"))
        email   = EmailTool(vault=vault)
    """

    def __init__(self) -> None:
        self._store: dict[str, Secret] = {}

    # ---- loaders -----------------------------------------------------------

    def set(self, name: str, value: str) -> None:
        """Programmatically set a secret (mostly for tests)."""
        self._store[name] = Secret(value=value, name=name)

    def load_from_env(self, *names: str, required: bool = False) -> None:
        """
        Load a subset of secrets directly from `os.environ`.

        If `required=True` — raises `SecretNotFoundError` for missing names.
        Otherwise — logs a warning and skips them.
        """
        for name in names:
            value = (os.environ.get(name) or "").strip()
            if not value:
                if required:
                    raise SecretNotFoundError(
                        f"Required secret '{name}' not in environment"
                    )
                logger.warning("[SecretsVault] env missing '%s'", name)
                continue
            self._store[name] = Secret(value=value, name=name)
            logger.debug(
                "[SecretsVault] loaded '%s' from env (len=%d)", name, len(value)
            )

    def load_dotenv(self, path: Path | str | None = None) -> int:
        """
        Load every key from a .env file via python-dotenv.

        Returns count of secrets loaded. Returns 0 if dotenv is missing or
        the file does not exist.
        """
        try:
            from dotenv import dotenv_values  # type: ignore
        except ImportError:
            logger.warning("[SecretsVault] python-dotenv not installed")
            return 0

        env_path = Path(path) if path else Path(".env")
        if not env_path.exists():
            logger.warning("[SecretsVault] .env not found at %s", env_path)
            return 0

        loaded = 0
        for key, value in dotenv_values(env_path).items():
            if not value:
                continue
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            self._store[key] = Secret(value=value, name=key)
            loaded += 1

        logger.info("[SecretsVault] loaded %d secrets from %s", loaded, env_path)
        return loaded

    def load_from_dpapi(self, encrypted_file: Path | str) -> int:
        """
        Windows-only: decrypt a DPAPI blob into in-memory secrets.

        The blob must have been produced by `encrypt_dotenv_to_dpapi()`
        on the same Windows user account. DPAPI uses the user's master key
        derived from their Windows password — a stolen laptop with a
        different login cannot decrypt it.
        """
        if sys.platform != "win32":
            raise OSError("DPAPI is only available on Windows")
        try:
            import win32crypt  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "pywin32 not installed. Install with: pip install pywin32"
            ) from exc

        path = Path(encrypted_file)
        if not path.exists():
            raise FileNotFoundError(f"Encrypted secrets blob not found: {path}")

        ciphertext = path.read_bytes()
        _, plaintext = win32crypt.CryptUnprotectData(
            ciphertext, None, None, None, 0
        )
        text = plaintext.decode("utf-8", errors="replace")

        loaded = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or not value:
                continue
            self._store[key] = Secret(value=value, name=key)
            loaded += 1

        logger.info("[SecretsVault] loaded %d secrets from DPAPI blob", loaded)
        return loaded

    # ---- accessors ---------------------------------------------------------

    def get(self, name: str) -> Secret:
        """Return a Secret or raise SecretNotFoundError."""
        if name not in self._store:
            raise SecretNotFoundError(
                f"Secret '{name}' not loaded. Loaded keys: {self.names()}"
            )
        return self._store[name]

    def get_optional(self, name: str) -> Secret | None:
        return self._store.get(name)

    def reveal(self, name: str) -> str:
        """Shortcut for vault.get(name).reveal(). Audit each call site."""
        return self.get(name).reveal()

    def has(self, name: str) -> bool:
        return name in self._store

    def names(self) -> list[str]:
        return sorted(self._store.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._store

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"SecretsVault(loaded={self.names()})"


# ────────────────────────────────────────────────────────────────────
# DPAPI migration helper (Windows only)
# ────────────────────────────────────────────────────────────────────

def encrypt_dotenv_to_dpapi(env_path: Path | str, output_path: Path | str) -> Path:
    """
    Read a plaintext .env file, encrypt it with DPAPI, write to `output_path`.

    Usage (one-off, on the Windows account that will run the agent):

        from brain.secrets import encrypt_dotenv_to_dpapi
        from pathlib import Path
        encrypt_dotenv_to_dpapi(Path(".env"), Path("config/secrets.dpapi"))
        # THEN delete .env from disk:
        Path(".env").unlink()

    Decrypt only works under the same Windows user account.
    """
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    try:
        import win32crypt  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "pywin32 not installed. Install with: pip install pywin32"
        ) from exc

    env = Path(env_path)
    out = Path(output_path)
    if not env.exists():
        raise FileNotFoundError(env)
    out.parent.mkdir(parents=True, exist_ok=True)

    plaintext = env.read_bytes()
    ciphertext = win32crypt.CryptProtectData(
        plaintext,
        "agent-secrets",   # description (visible to DPAPI tooling, not sensitive)
        None,              # optional entropy
        None,
        None,
        0,                 # flags — 0 = current user only
    )
    out.write_bytes(ciphertext)
    logger.info(
        "[SecretsVault] encrypted %s -> %s (%d -> %d bytes)",
        env, out, len(plaintext), len(ciphertext),
    )
    return out
