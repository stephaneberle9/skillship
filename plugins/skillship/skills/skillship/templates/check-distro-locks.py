#!/usr/bin/env python3
"""Fail when a distro's lock no longer matches its profile and the skills it ships.

Runs `skillship lock --all --check` (https://github.com/stephaneberle9/skillship)
at the version pinned in `distros/.skillship-version`, through `uvx`, or `pipx`
where uv is missing, as on GitHub's runners. A lock
lists the skills and files each distro delivers, so this fails exactly when a
commit changes what a recipient would receive: a new `metadata.requires`, a
skill matching an `include:` glob, a renamed or deleted file. Recompute the
lock with `uvx skillship@<version> lock <name>`, review its diff, and commit it.
Editing the text of a file that already ships does not fail it.

To try an unreleased skillship, set `SKILLSHIP_FROM` to its checkout for the
session: the check then runs that working tree instead of the pinned release,
and says so. CI never sets it, so a lock only an unreleased skillship can read
still fails there until the pin moves.

In GitHub Actions the report also goes into the job summary.

Usage:
    python check-distro-locks.py REPO_ROOT [--base REF]   # --base is accepted and ignored

Exit status: 0 = every lock is current (or there are no distros), 1 = a lock is
stale or cannot be computed, or neither uv nor pipx is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(
        sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "."
    )
    distros = root / "distros"
    if not any(distros.glob("*/distro.yaml")):
        print("no distros")
        return 0
    pin = distros / ".skillship-version"
    version = pin.read_text(encoding="utf-8").strip() if pin.is_file() else ""
    if not version:
        print("  FAIL    distros/.skillship-version is missing or empty")
        return 1
    args = ["-C", str(root), "lock", "--all", "--check"]
    rerun = f"uvx skillship@{version}"
    checkout = os.environ.get("SKILLSHIP_FROM", "").strip()
    if checkout:
        # an unreleased skillship: `uv run --project` uses the checkout as it is on disk,
        # where `uvx --from <dir>` would reuse a build made before its sources changed
        if not (Path(checkout) / "pyproject.toml").is_file() or not shutil.which("uv"):
            print(
                f"  FAIL    SKILLSHIP_FROM={checkout} is no skillship checkout, or uv is missing"
            )
            return 1
        print(
            f"  NOTE    SKILLSHIP_FROM is set: checking with the checkout {checkout}, not the pinned {version}"
        )
        version = f"checkout {checkout}"
        rerun = f"uv run --project {checkout} skillship"
        argv = ["uv", "run", "--quiet", "--project", checkout, "skillship", *args]
    # uvx where uv is installed; pipx on GitHub's runners, which ship it but not uv
    elif shutil.which("uvx"):
        argv = ["uvx", "--quiet", "--from", f"skillship=={version}", "skillship", *args]
    elif shutil.which("pipx"):
        argv = [
            "pipx",
            "run",
            "--quiet",
            "--spec",
            f"skillship=={version}",
            "skillship",
            *args,
        ]
    else:
        print(
            "  FAIL    neither uvx nor pipx found: install uv (https://docs.astral.sh/uv/) to check the distro locks"
        )
        return 1
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    report = (done.stdout + done.stderr).rstrip()
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Distro locks (skillship {version})\n\n```\n{report}\n```\n")
    if done.returncode != 0:
        print(
            f"  FAIL    a distro lock is stale: run `{rerun} lock <name>`, review and commit it"
        )
        return 1
    print("  OK      every distro lock is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
