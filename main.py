"""CLI entry point: ``python main.py`` opens the REPL; with ``--ask "..."`` it answers once."""

from __future__ import annotations

from cli.app import run_cli


def main() -> int:
    """Launch the CLI. Everything it does lives in cli/app.py."""
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
