"""Command line entry point.

Exit status: 0 on success, 1 when the input is wrong or a lock is stale, 2 on
a usage error or when something is asked of this version that it cannot do
yet. `deliver` and `drift` are declared but not implemented yet.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from skillship import __version__, lockfile, profile
from skillship.build import build
from skillship.errors import SkillshipError
from skillship.resolve import Resolution, check, closure, resolve
from skillship.source import SourceRepo

NOT_IMPLEMENTED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillship",
        description='Resolve, lock and deliver curated subsets ("distros") of Agent Skills.',
    )
    parser.add_argument(
        "--version", action="version", version=f"skillship {__version__}"
    )
    parser.add_argument(
        "-C",
        dest="repo",
        metavar="DIR",
        help="the source repo (default: the git work tree around the current directory)",
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

    build_cmd = commands.add_parser(
        "build", help="build a distro's staging tree and archives from its lock"
    )
    build_cmd.add_argument("name", help="distro name")
    build_cmd.add_argument(
        "--out",
        default="dist",
        help="output directory (default: dist/ in the source repo)",
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
    if args.command in ("deliver", "drift"):
        print(f"skillship {args.command}: not implemented yet", file=sys.stderr)
        return NOT_IMPLEMENTED
    try:
        repo = SourceRepo(_source_root(args.repo))
        if args.command == "lock":
            return _lock(repo, args)
        return _build(repo, args)
    except SkillshipError as exc:
        print(f"skillship {args.command}: {exc}", file=sys.stderr)
        return exc.exit_code


def _source_root(given: str | None) -> Path:
    if given:
        return Path(given)
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return Path.cwd()
    return Path(done.stdout.strip()) if done.returncode == 0 else Path.cwd()


def _lock(repo: SourceRepo, args: argparse.Namespace) -> int:
    if args.explain:
        print(
            "skillship lock: --explain has nothing to show: flattening is not implemented yet",
            file=sys.stderr,
        )
    names = profile.distro_names(repo.root) if args.all else [args.name]
    if not names:
        raise SkillshipError(f"no distros under {repo.root / profile.DISTROS_DIR}")
    status = 0
    for name in names:
        prof = profile.load(repo.root, name)
        profile.require_implemented(prof)
        res = resolve(repo, prof)
        _report(repo, prof, res)
        check(res)
        text = lockfile.render(prof, res)
        if args.check:
            diff = lockfile.stale_diff(repo.root, prof, text)
            if diff:
                print(
                    f"{name}: lock is stale -- run `skillship lock {name}` and commit the result"
                )
                print(diff, end="")
                status = 1
            else:
                print(f"{name}: lock is current")
        elif lockfile.write(repo.root, prof, text):
            print(f"{name}: wrote {profile.DISTROS_DIR}/{name}/{profile.LOCK}")
        else:
            print(f"{name}: lock unchanged")
    return status


def _report(repo: SourceRepo, prof: profile.Profile, res: Resolution) -> None:
    files = sum(len(f) for f in res.files.values()) + len(res.extras)
    print(f"{prof.name}: {len(res.skills)} skills, {files} files")
    if res.suggested:
        print("  suggested by a shipped skill, not shipped:")
        for ref, by in sorted(res.suggested.items()):
            print(f"    {ref}  (suggested by {', '.join(sorted(by))})")
            print(f"      to ship it: include: [{ref}]{_adds(repo, res, ref)}")
    if res.dangling:
        counts: dict[str, int] = {}
        for d in res.dangling:
            counts[d.status] = counts.get(d.status, 0) + 1
        summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        print(f"  mentions of skills that do not ship: {summary}")
    blocking = sorted({d.ref for d in res.blocking})
    for ref in blocking:
        print(f"  blocking: `{ref}` does not ship, but is named at")
        for d in res.blocking:
            if d.ref == ref:
                print(f"      {d.at}")
        print(
            "    fix it with one of these profile edits, or reword the mention in the source:"
        )
        print(f"      include: [{ref}]{_adds(repo, res, ref)}")
        print(f"      acknowledge: [{ref}]")


def _adds(repo: SourceRepo, res: Resolution, ref: str) -> str:
    new = [s for s in closure(repo, ref) if s not in res.skills]
    return f"   # adds {', '.join(new)}" if len(new) > 1 else ""


def _build(repo: SourceRepo, args: argparse.Namespace) -> int:
    prof = profile.load(repo.root, args.name)
    profile.require_implemented(prof)
    res = resolve(repo, prof)
    check(res)
    if lockfile.stale_diff(repo.root, prof, lockfile.render(prof, res)):
        raise SkillshipError(
            f"the lock of `{args.name}` is stale -- run `skillship lock {args.name}`, "
            "review the diff and commit it, then build"
        )
    data, digest = lockfile.load(repo.root, args.name)
    out = Path(args.out)
    if not out.is_absolute():
        out = repo.root / out
    result = build(repo, prof, data, digest, out)
    if result.dirty:
        print(
            f"warning: the source repo has uncommitted changes; {result.source} "
            "does not reproduce this build",
            file=sys.stderr,
        )
    print(f"{args.name}: staged {result.staging}")
    for archive in result.archives:
        print(f"{args.name}: wrote {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
