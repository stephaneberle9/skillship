"""The git and forge commands `deliver` and `drift` run: clone, commit, push, open a PR or MR.

Credentials are never skillship's: a clone or push authenticates through the
git credential helper, a pull request through `gh`, a merge request through
`glab`, each as already logged in. A commit is signed with plain
`git commit -S`, so the user's git configuration picks SSH or GPG.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from skillship.errors import SkillshipError

# Added to the environment of every command this module runs: the CI
# credentials of `ci.credentials`, while a delivery runs.
EXTRA_ENV: dict[str, str] = {}


def _env() -> dict[str, str] | None:
    return {**os.environ, **EXTRA_ENV} if EXTRA_ENV else None


def run(
    cwd: Path, *args: str, check: bool = True, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=input,
            env=_env(),
            check=False,
        )
    except OSError as exc:
        raise SkillshipError(f"cannot run `{args[0]}`: {exc}") from exc
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()
        raise SkillshipError(f"`{' '.join(args)}` failed:\n{detail}")
    return done


def run_bytes(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """A command whose output is bytes, line endings untouched."""
    try:
        return subprocess.run(
            list(args), cwd=cwd, capture_output=True, env=_env(), check=False
        )
    except OSError as exc:
        raise SkillshipError(f"cannot run `{args[0]}`: {exc}") from exc


def has_commit(cwd: Path, sha: str) -> bool:
    return (
        run(cwd, "git", "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode
        == 0
    )


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return run(cwd, "git", *args, check=check).stdout


def clone(url: str, branch: str | None, dest: Path) -> str:
    """A shallow clone of the target branch, with files exactly as committed; returns the branch."""
    args = [
        "clone", "--quiet", "--depth", "1", "--no-tags",
        "-c", "core.autocrlf=false", "-c", "core.eol=lf",
    ]  # fmt: skip
    if branch:
        args += ["--branch", branch]
    done = run(dest.parent, "git", *args, url, str(dest), check=False)
    if done.returncode != 0:
        where = f"branch `{branch}` of {url}" if branch else url
        raise SkillshipError(
            f"cannot clone {where}:\n{done.stderr.strip()}\n"
            "The target branch must exist and hold at least one commit."
        )
    head = git(dest, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if head == "HEAD" or not git(
        dest, "rev-parse", "--verify", "-q", "HEAD", check=False
    ):
        raise SkillshipError(
            f"{url} has no commit on the target branch -- push an initial commit first"
        )
    return branch or head


def patch(path: str, before: bytes, after: bytes) -> bytes:
    """A `git apply`-able patch of `path` from `before` to `after`."""
    work = Path(tempfile.mkdtemp(prefix="skillship-diff-"))
    try:
        for side, data in (("a", before), ("b", after)):
            p = work / side / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        done = run_bytes(
            work, "git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "diff",
            "--no-index", "--no-prefix", "--no-color", "--binary", "--full-index",
            f"a/{path}", f"b/{path}",
        )  # fmt: skip
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if done.returncode not in (0, 1):
        raise SkillshipError(
            f"`git diff` failed: {done.stderr.decode(errors='replace').strip()}"
        )
    return done.stdout


def open_pull_request(
    clone_dir: Path, forge: str, base: str, head: str, title: str, body: str
) -> str:
    """Open a pull or merge request from `head` into `base`; returns its URL.

    An open one is reused, its description replaced by `body`, which describes the
    branch as the delivery just rewrote it.
    """
    if forge == "github":
        listed = run(
            clone_dir, "gh", "pr", "list", "--head", head, "--base", base,
            "--state", "open", "--json", "url",
        ).stdout  # fmt: skip
        found = json.loads(listed or "[]")
        if found:
            url = found[0]["url"]
            run(clone_dir, "gh", "pr", "edit", url, "--body-file", "-", input=body)
            return url
        out = run(
            clone_dir, "gh", "pr", "create", "--base", base, "--head", head,
            "--title", title, "--body-file", "-", input=body,
        ).stdout  # fmt: skip
        return out.strip().splitlines()[-1] if out.strip() else ""
    listed = run(
        clone_dir, "glab", "mr", "list", "--source-branch", head,
        "--target-branch", base, "--output", "json",
    ).stdout  # fmt: skip
    found = json.loads(listed or "[]")
    if found:
        run(
            clone_dir, "glab", "mr", "update", str(found[0]["iid"]),
            "--description-file", "-", "--yes", input=body,
        )  # fmt: skip
        return found[0].get("web_url", "")
    out = run(
        clone_dir, "glab", "mr", "create", "--source-branch", head,
        "--target-branch", base, "--title", title, "--description-file", "-",
        "--yes", "--remove-source-branch", input=body,
    ).stdout  # fmt: skip
    urls = [w for w in out.split() if w.startswith("http")]
    return urls[-1] if urls else out.strip()
