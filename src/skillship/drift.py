"""Recipient-side changes to delivered files, as patches against the source repo.

For each `owned` file the recipient changed since the last delivery, `drift`
takes three versions of it: *base*, the file as delivered from the source
commit the record names; *theirs*, the recipient's file; and *ours*, the source
file today. Because the delivered copy may be rewritten (pruned `suggests`,
flattened plugin names), the recipient's change is first carried back onto the
unrewritten source file at that commit, by a three-way merge with the delivered
base, so a rewrite never shows up as a recipient change. A second three-way
merge carries it onto *ours*. The patch is `git apply`-able in the source repo.

| Verdict           | Meaning |
| ----------------- | ------- |
| `clean`           | The patch applies to the source as it is today |
| `conflicting`     | The patch holds conflict markers to resolve by hand |
| `already upstream`| The source already holds the recipient's change |
| `deleted`         | The recipient deleted the file; no patch |
| `generated`       | skillship writes the file itself; no source to merge back into |
| `no base`         | The delivered base cannot be rebuilt (a delivery from uncommitted changes, or a source commit this clone lacks) |
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skillship import ci, config, gitops
from skillship.build import normalized
from skillship.deliver import (
    Drifted,
    base_delivery,
    content_hash,
    drift,
    inside,
    read_record,
    remove_worktree,
    rmtree,
)
from skillship.errors import SkillshipError
from skillship.profile import Profile
from skillship.source import SourceRepo


@dataclass
class Finding:
    path: str  # in the target repo
    source: str | None  # in the source repo
    verdict: str
    patch: Path | None = None


def drift_report(
    repo: SourceRepo, profile: Profile, target: dict[str, Any], out_dir: Path
) -> list[Finding]:
    """The recipient's changes in one `git` target, with a patch per mergeable file in `out_dir`."""
    forge = config.forge_of(target)
    tmp = Path(tempfile.mkdtemp(prefix="skillship-"))
    try:
        clone = tmp / "target"
        with ci.credentials(target["url"], forge):
            gitops.clone(target["url"], target.get("branch"), clone)
        record = read_record(clone, profile.name)
        drifted = drift(clone, record)
        if not drifted:
            return []
        built = base_delivery(repo, profile, record, forge, tmp / "base")
        base = (built[0], built[1].files) if built else None
        findings = []
        for d in drifted:
            findings.append(_finding(repo, clone, record, base, d, out_dir))
        return findings
    finally:
        remove_worktree(repo, tmp / "base")
        rmtree(tmp)


def _finding(
    repo: SourceRepo,
    clone: Path,
    record: dict[str, Any],
    base: tuple[SourceRepo, dict[str, Any]] | None,
    d: Drifted,
    out_dir: Path,
) -> Finding:
    if d.change == "deleted":
        return Finding(d.path, None, "deleted")
    if base is None:
        return Finding(d.path, None, "no base")
    base_repo, files = base
    delivered = files.get(d.path)
    if delivered is None or delivered.source is None:
        return Finding(d.path, None, "generated")
    if content_hash(d.path, delivered.data) != record["files"][d.path].get("sha256"):
        return Finding(d.path, delivered.source, "no base")
    src = delivered.source
    current = repo.root / src
    if not current.is_file():
        return Finding(d.path, src, "removed from the source")
    base_source = normalized((base_repo.root / src).read_bytes())
    theirs = normalized(inside(clone, d.path).read_bytes())
    ours_raw = current.read_bytes()
    crlf = b"\r\n" in ours_raw
    ours = normalized(ours_raw)

    work = Path(tempfile.mkdtemp(prefix="skillship-merge-"))
    try:
        # 1. the recipient's change, carried from the delivered copy onto the unrewritten source
        theirs_source, c1 = _unrewrite(work, base_source, delivered.data, theirs)
        # 2. and from there onto the source as it is today
        merged, c2 = _merge(work, ours, base_source, theirs_source, "recipient")
        if crlf:
            merged = merged.replace(b"\n", b"\r\n")
        if merged == ours_raw:
            return Finding(d.path, src, "already upstream")
        patch = gitops.patch(src, ours_raw, merged)
    finally:
        rmtree(work)
    dest = out_dir / f"{src}.patch"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(patch)
    return Finding(d.path, src, "conflicting" if c1 or c2 else "clean", dest)


# Words, runs of whitespace, single other characters: the units of the first merge.
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")
_NL = "\ue000"  # stands for a line break inside a token while the merge runs


def _unrewrite(
    work: Path, base_source: bytes, base_delivered: bytes, theirs: bytes
) -> tuple[bytes, int]:
    """The recipient's file with the delivery's rewrites undone, and the number of conflicts.

    The rewrites change words inside lines, as a recipient's edit may on the same
    line, so this merge runs on tokens: a flattened qualifier and an edit next to
    it merge cleanly. Only when the recipient changed a rewritten token itself does
    it conflict, and the line-level merge then shows where.
    """
    if base_source == base_delivered:
        return theirs, 0
    try:
        texts = [t.decode("utf-8") for t in (base_source, base_delivered, theirs)]
    except UnicodeDecodeError:
        texts = []
    if texts and not any(_NL in t for t in texts):
        encoded = [_tokens(t) for t in texts]
        merged, conflicts = _merge(
            work, encoded[0], encoded[1], encoded[2], "delivered"
        )
        if not conflicts:
            return _untokens(merged), 0
    return _merge(work, base_source, base_delivered, theirs, "delivered")


def _tokens(text: str) -> bytes:
    """One token per line, a line break inside a token stood in for."""
    return "".join(t.replace("\n", _NL) + "\n" for t in _TOKEN.findall(text)).encode()


def _untokens(data: bytes) -> bytes:
    return data.decode("utf-8").replace("\n", "").replace(_NL, "\n").encode("utf-8")


def _merge(
    work: Path, current: bytes, base: bytes, other: bytes, label: str
) -> tuple[bytes, int]:
    """`git merge-file`: the change base -> other applied to current, and the number of conflicts."""
    paths = []
    for name, data in (("current", current), ("base", base), ("other", other)):
        p = work / name
        p.write_bytes(data)
        paths.append(str(p))
    done = gitops.run_bytes(
        work, "git", "merge-file", "-p", "-L", "source", "-L", "base", "-L", label, *paths,
    )  # fmt: skip
    if done.returncode < 0 or done.returncode > 127:
        raise SkillshipError(
            f"`git merge-file` failed: {done.stderr.decode(errors='replace').strip()}"
        )
    return done.stdout, done.returncode


def clear(out_dir: Path) -> None:
    if out_dir.is_symlink():
        raise SkillshipError(
            f"{out_dir} is a symlink; drift writes only into a real directory"
        )
    if out_dir.exists():
        shutil.rmtree(out_dir)
