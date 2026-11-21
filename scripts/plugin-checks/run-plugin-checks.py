#!/usr/bin/env python3
"""Run every plugin check of this repo: the shared ones next to this file, then the local ones.

    python scripts/plugin-checks/run-plugin-checks.py [--base REF] [--root DIR] [--shared-only]

The shared checks (`check-*.py` next to this file) come from the `claude-plugin-knowledge`
skill of the skill-crafting plugin and are kept byte-identical to it; `/claude-plugin-audit`
updates them. Checks of this repo's own go in `scripts/plugin-checks/local/` as
`check-*.py`: each is run with the repo root as its only argument, plus `--base REF` when
given, and fails by exiting non-zero.

`--base` (the target branch, e.g. `origin/main`) enables the checks that compare against
it. Without it, a pull request pipeline on GitHub (`GITHUB_BASE_REF`) or a merge request
pipeline on GitLab (`CI_MERGE_REQUEST_TARGET_BRANCH_NAME`) supplies it: the script then
fetches the target branch and, in a shallow clone, the history the comparison needs, so
the CI job that runs the hooks keeps its default shallow checkout. A local commit sets
neither, so the pre-commit hook compares against nothing. Every check runs even after one
fails, so a single run reports everything.
`--root` checks another repository than the one this file sits in -- that is how the
skills run the current templates against a repo before seeding them. `--shared-only` skips
the local checks: a run from outside the checked repo, such as skillship's delivery gate,
then executes no code of that repo's.

Exit status: 0 = every check passed, 1 = at least one failed.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TAKES_BASE = {"check-plugin-manifests.py"}              # shared checks that accept --base


def ci_base(root: str) -> str | None:
    """The target branch of a pull or merge request pipeline, fetched; None elsewhere."""
    branch = os.environ.get("GITHUB_BASE_REF") or os.environ.get(
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME")
    if not branch:
        return None
    def git(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", root, *argv], capture_output=True, text=True)
    shallow = git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    done = git("fetch", "--quiet", "--no-tags", *(["--unshallow"] if shallow else []),
               "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if done.returncode != 0:  # the check that needs it reports BROKEN with the reason
        print(f"cannot fetch the target branch {branch}: {done.stderr.strip()}", flush=True)
    return f"origin/{branch}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="ref to compare against, e.g. origin/main")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(HERE)),
                    help="repository to check (default: the one holding scripts/plugin-checks/)")
    ap.add_argument("--shared-only", action="store_true",
                    help="skip the local checks, which are code of the checked repo")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    base = args.base or ci_base(root)

    shared = sorted(glob.glob(os.path.join(HERE, "check-*.py")))
    local = [] if args.shared_only else sorted(
        glob.glob(os.path.join(root, "scripts", "plugin-checks", "local", "check-*.py")))
    failed = []
    for script in shared + local:
        name = os.path.basename(script)
        argv = [sys.executable, script, root]
        if base and (script in local or name in TAKES_BASE):
            argv += ["--base", base]
        label = "local/" + name if script in local else name
        print(f"\n##### {label}", flush=True)
        if subprocess.run(argv, cwd=root).returncode != 0:
            failed.append(label)

    print("\n#####", "all plugin checks passed" if not failed else "failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
