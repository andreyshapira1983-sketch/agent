"""CLI entry point for the agent.

# Interactive — multi-turn dialogue with both memories python main.py > What
is DuckDuckGo? > And who founded it? # follow-up; planner reuses turn 1 >
:remember preference,fact I prefer concise answers in Russian > :ingest-
source knowledge/doctrine/ROADMAP.md > :ingest-project . --limit 40 --dry-
run > :source-library books > :ingest-web "autonomous agent" --sources
wikis,science --limit 3 --dry-run > :ingest-rss
https://www.python.org/blogs/rss/ --limit 5 --dry-run > :connectors >
:connector-plan "monitor Python releases" > :memory # inspect working +
persistent memory > :forget mem_abc123 # delete one persistent record >
:forget # delete ALL persistent records > :clear # wipe working memory only
> :quit
"""

from __future__ import annotations

from cli.app import run_cli


def main() -> int:
    """Launch the CLI. Everything it does lives in cli/app.py."""
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
