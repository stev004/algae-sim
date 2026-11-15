"""Azure helpers.

    uv run python -m algae_sim.azure schema [--database algae] > algae_sim/azure/schema.kql
"""

from __future__ import annotations

import argparse

from algae_sim.azure.records import DATABASE_DEFAULT, schema_kql


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m algae_sim.azure")
    sub = parser.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema", help="print the Data Explorer setup script")
    schema.add_argument("--database", default=DATABASE_DEFAULT)
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(schema_kql(args.database), end="")


if __name__ == "__main__":
    main()
