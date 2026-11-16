"""Build a distro from its lock: the staging tree, `.skillship.json` and the archives.

The zip is deterministic: entries sorted, one fixed timestamp, Unix permissions
from git, and text files with LF line endings. Two builds of the same lock from
the same source commit produce the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from skillship.errors import NotImplementedYet, SkillshipError
from skillship.profile import BUILD_TARGETS_IMPLEMENTED, Profile
from skillship.resolve import LICENSE_FILES
from skillship.source import SourceRepo

RECORD = ".skillship.json"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Targets `build` has nothing to do for: `deliver` handles them.
DELIVER_ONLY = ("git",)


@dataclass
class BuildResult:
    staging: Path
    archives: list[Path]
    source: str
    dirty: bool


def build(
    repo: SourceRepo,
    profile: Profile,
    lock: dict[str, Any],
    lock_digest: str,
    out: Path,
) -> BuildResult:
    for target in profile.targets:
        kind = target["type"]
        if kind not in BUILD_TARGETS_IMPLEMENTED + DELIVER_ONLY:
            raise NotImplementedYet(f"target type `{kind}` is not implemented yet")

    files = _delivered(repo, lock)
    sha, dirty = repo.head()
    source = f"{repo.name()}@{sha}" if sha else repo.name()

    base = out / profile.name
    staging = base / "staging"
    for path in (out, base, staging):
        if path.is_symlink():
            raise SkillshipError(
                f"{path} is a symlink; build writes only into a real directory"
            )
    if staging.exists():
        shutil.rmtree(staging)
    record_files: dict[str, dict[str, str]] = {}
    contents: dict[str, tuple[bytes, int]] = {}
    for dest, src in files.items():
        data = _normalized((repo.root / src).read_bytes())
        contents[dest] = (data, repo.mode(src))
        record_files[dest] = {
            "ownership": "owned",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    record: dict[str, Any] = {
        "distro": profile.name,
        "source": source,
        "lock-digest": lock_digest,
        "files": dict(sorted(record_files.items())),
    }
    if dirty:
        record["dirty"] = True
    contents[RECORD] = (
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(),
        0o644,
    )

    for dest, (data, _) in contents.items():
        path = staging / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    archives = []
    for target in profile.targets:
        if target["type"] == "archive":
            archive = base / f"{target.get('name', profile.name)}.zip"
            _zip(archive, contents)
            archives.append(archive)
    return BuildResult(staging, archives, source, dirty)


def _delivered(repo: SourceRepo, lock: dict[str, Any]) -> dict[str, str]:
    """Destination path in the delivery -> source path in the repo, for every delivered file."""
    layout = lock.get("layout", {})
    if layout.get("shape") != "skills":
        raise NotImplementedYet(
            f"layout shape `{layout.get('shape')}` is not implemented yet"
        )
    root = PurePosixPath(layout.get("root", "."))
    if root.is_absolute() or ".." in root.parts:
        raise SkillshipError(
            f"the lock's layout root `{root}` leaves the delivery tree"
        )
    files: dict[str, str] = {}
    for entry in lock.get("skills", []):
        skill = repo.skills.get(entry["id"])
        if skill is None:
            raise SkillshipError(
                f"the lock ships `{entry['id']}`, which is no skill of the source repo"
            )
        for rel in entry["files"]:
            src = f"{repo.rel(skill.path)}/{rel}"
            if ".." in PurePosixPath(rel).parts or repo.is_symlink(src):
                raise SkillshipError(
                    f"the lock ships `{src}`, which is not a plain file of the skill"
                )
            if not (repo.root / src).is_file():
                raise SkillshipError(f"the lock ships `{src}`, which does not exist")
            files[(root / "skills" / skill.name / rel).as_posix()] = src
    for extra in lock.get("extras", []):
        if extra not in LICENSE_FILES or repo.is_symlink(extra):
            raise SkillshipError(
                f"the lock ships `{extra}`, which is no license file of the source repo"
            )
        files[(root / extra).as_posix()] = extra
    return files


def _normalized(data: bytes) -> bytes:
    """Text with LF line endings; binary (anything with a NUL byte) unchanged."""
    if b"\0" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def _zip(path: Path, contents: dict[str, tuple[bytes, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written to a fresh file and moved into place: `os.replace` swaps the
    # directory entry, so a symlink planted at `path` is replaced, never followed.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".zip.part")
    try:
        with os.fdopen(fd, "wb") as fh:
            _write_zip(fh, contents)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_zip(fh: Any, contents: dict[str, tuple[bytes, int]]) -> None:
    with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(contents):
            data, mode = contents[name]
            info = zipfile.ZipInfo(name, ZIP_EPOCH)
            info.create_system = 3  # Unix, so the permission bits are honored
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
