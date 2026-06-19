"""CLI command layer for the agent REPL.

Modules under ``cli/`` hold the argument parsers and ``:command`` handlers
that were split out of the very large ``main.py``. ``main.py`` re-exports the
public names so existing imports (and tests) keep working unchanged.
"""
