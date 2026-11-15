"""Command line entry point.

The four commands are declared here so their names and arguments are settled
before the machinery behind them exists. Until a command is implemented it
says so and exits with status 2, which no successful run ever returns.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from skillship import __version__

NOT_IMPLEMENTED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillship",
        description='Resolve, lock and deliver curated subsets ("distros") of Agent Skills.',
    )
    parser.add_argument(
        "--version", action="version", version=f"skillship {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    lock = commands.add_parser(
        "lock", help="resolve a distro's profile into its lock file"
    )
    which = lock.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "name", nargs="?", help="distro name, a directory under distros/"
    )
    which.add_argument(
        "--all", action="store_true", help="every distro of the source repo"
    )
    lock.add_argument(
        "--check",
        action="store_true",
        help="fail when a lock is stale instead of rewriting it",
    )
    lock.add_argument(
        "--explain",
        action="store_true",
        help="print every flatten rewrite as a before/after line",
    )

    build = commands.add_parser(
        "build", help="build a distro's staging tree and archives from its lock"
    )
    build.add_argument("name", help="distro name")
    build.add_argument(
        "--out", default="dist", help="output directory (default: dist/)"
    )

    deliver = commands.add_parser(
        "deliver", help="run a distro's targets: archives, uploads, git"
    )
    deliver.add_argument("name", help="distro name")
    deliver.add_argument("--target", help="run this target only")
    deliver.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be delivered, write nothing",
    )

    drift = commands.add_parser(
        "drift", help="report recipient-side changes and write merge-back patches"
    )
    drift.add_argument("name", help="distro name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"skillship {args.command}: not implemented yet", file=sys.stderr)
    return NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())
