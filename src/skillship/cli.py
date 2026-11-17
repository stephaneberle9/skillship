"""Command line entry point.

Exit status: 0 on success, 1 when the input is wrong, a lock is stale, a
delivery is refused or `drift` finds recipient changes, 2 on a usage error or
when something is asked of this version that it cannot do yet.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from skillship import __version__, config, lockfile, profile
from skillship.build import build
from skillship.deliver import deliver_git
from skillship.drift import clear, drift_report
from skillship.errors import NotImplementedYet, SkillshipError
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
        help="print every rewrite of the delivered copy as before/after lines",
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
    deliver.add_argument(
        "--target",
        metavar="TYPE",
        help="run the targets of this type only (archive, git)",
    )
    deliver.add_argument(
        "--dry-run",
        action="store_true",
        help="prepare and check the delivery in a scratch clone, push nothing",
    )
    deliver.add_argument(
        "--overwrite",
        action="append",
        default=[],
        metavar="PATH",
        help="replace this recipient-changed file of a git target (repeatable)",
    )
    deliver.add_argument(
        "--out",
        default="dist",
        help="output directory of archive targets (default: dist/ in the source repo)",
    )

    drift = commands.add_parser(
        "drift", help="report recipient-side changes and write merge-back patches"
    )
    drift.add_argument("name", help="distro name")
    drift.add_argument(
        "--out",
        default="dist",
        help="where the patches go, under <out>/<name>/drift/ (default: dist/)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo = SourceRepo(_source_root(args.repo))
        if args.command == "lock":
            return _lock(repo, args)
        if args.command == "deliver":
            return _deliver(repo, args)
        if args.command == "drift":
            return _drift(repo, args)
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
    names = profile.distro_names(repo.root) if args.all else [args.name]
    if not names:
        raise SkillshipError(f"no distros under {repo.root / profile.DISTROS_DIR}")
    status = 0
    for name in names:
        prof = profile.load(repo.root, name)
        profile.require_implemented(prof)
        res = resolve(repo, prof)
        _report(repo, prof, res)
        if args.explain:
            _explain(prof, res)
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
    generated = f", {len(res.generated)} generated" if res.generated else ""
    print(f"{prof.name}: {len(res.skills)} skills, {files} files{generated}")
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
    if res.rewritten:
        print(
            f"  rewritten: {sum(res.rewritten.values())} rewrites in "
            f"{len(res.rewritten)} files (`--explain` lists them)"
        )
    plugins = sorted({u.plugin for u in res.blocking_unflattened})
    for plugin in plugins:
        print(
            f"  blocking: plugin `{plugin}` is named where flattening cannot rewrite it, at"
        )
        for u in res.blocking_unflattened:
            if u.plugin == plugin:
                print(f"      {u.at}")
        print(
            '    reword the mention in the source ("the `skill` skill (`plugin` plugin)" '
            "is flattened), or accept it as it is:"
        )
        print(f"      acknowledge: [{plugin}]")


def _explain(prof: profile.Profile, res: Resolution) -> None:
    if not res.rewrites:
        print("  the delivered copy is not rewritten")
    for path, rewrites in sorted(res.rewrites.items()):
        for r in rewrites:
            print(f"  {path}:{r.line}")
            for line in r.before.split("\n"):
                print(f"    - {line}")
            for line in r.after.split("\n"):
                print(f"    + {line}")


def _adds(repo: SourceRepo, res: Resolution, ref: str) -> str:
    new = [s for s in closure(repo, ref) if s not in res.skills]
    return f"   # adds {', '.join(new)}" if len(new) > 1 else ""


def _current_lock(
    repo: SourceRepo, name: str, then: str
) -> tuple[profile.Profile, dict, str]:
    """The profile and its lock, refused when the lock no longer matches the source."""
    prof = profile.load(repo.root, name)
    profile.require_implemented(prof)
    res = resolve(repo, prof)
    check(res)
    if lockfile.stale_diff(repo.root, prof, lockfile.render(prof, res)):
        raise SkillshipError(
            f"the lock of `{name}` is stale -- run `skillship lock {name}`, "
            f"review the diff and commit it, then {then}"
        )
    data, digest = lockfile.load(repo.root, name)
    return prof, data, digest


def _out_dir(repo: SourceRepo, given: str) -> Path:
    out = Path(given)
    return out if out.is_absolute() else repo.root / out


def _build(repo: SourceRepo, args: argparse.Namespace) -> int:
    prof, data, digest = _current_lock(repo, args.name, "build")
    result = build(repo, prof, data, digest, _out_dir(repo, args.out))
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


def _deliver(repo: SourceRepo, args: argparse.Namespace) -> int:
    prof, data, digest = _current_lock(repo, args.name, "deliver")
    targets = [t for t in prof.targets if not args.target or t["type"] == args.target]
    if not targets:
        kind = f"{args.target} " if args.target else ""
        raise SkillshipError(f"`{args.name}` has no {kind}target")
    for t in targets:
        if t["type"] not in profile.DELIVER_TARGETS_IMPLEMENTED:
            raise NotImplementedYet(f"target type `{t['type']}` is not implemented yet")
    if any(t["type"] == "archive" for t in targets):
        if args.dry_run:
            print(f"{args.name}: would build the archives into {args.out}/")
        else:
            result = build(repo, prof, data, digest, _out_dir(repo, args.out))
            for archive in result.archives:
                print(f"{args.name}: wrote {archive}")
    for t in targets:
        if t["type"] == "git":
            outcome = deliver_git(
                repo,
                prof,
                data,
                digest,
                t,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            )
            for line in outcome.lines:
                print(line)
    return 0


def _drift(repo: SourceRepo, args: argparse.Namespace) -> int:
    prof = profile.load(repo.root, args.name)
    targets = config.git_targets(prof)
    if not targets:
        raise SkillshipError(f"`{args.name}` has no git target")
    out = _out_dir(repo, args.out) / args.name / "drift"
    clear(out)
    status = 0
    for t in targets:
        findings = drift_report(repo, prof, t, out)
        if not findings:
            print(f"{t['url']}: no drift")
            continue
        status = 1
        print(f"{t['url']}: the recipient changed {len(findings)} delivered files")
        for f in findings:
            where = f" <- {f.source}" if f.source else ""
            patch = f"  ({f.patch})" if f.patch else ""
            print(f"  {f.verdict:<17} {f.path}{where}{patch}")
    if status:
        print(
            "Apply a patch in the source repo with `git apply <patch>` and commit it, "
            "or discard the change with `skillship deliver --overwrite PATH`."
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
