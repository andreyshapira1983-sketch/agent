"""Allow `python -m project_intelligence` (CLI not fully implemented yet)."""

from project_intelligence.db.migrate import main as migrate_main


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        migrate_main(sys.argv[2:])
        return
    print(
        "project_intelligence: only `migrate` is available in this slice.\n"
        "Usage: python -m project_intelligence migrate [--db PATH]"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()