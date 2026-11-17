"""The source repo's own settings, `distros/skillship.yaml`, shared by all its distros.

Today it holds `target-files`: named sets of non-skill files that a `git` target
in a plugin shape receives, each with the ownership it is delivered with and an
optional `gate`, the command `deliver` runs against the target clone before
pushing.
skillship knows nothing about what the files are; the source repo says so here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator

from skillship.errors import SkillshipError
from skillship.profile import DISTROS_DIR, Profile
from skillship.source import SourceRepo

SETTINGS = "skillship.yaml"


@dataclass
class TargetFile:
    path: str  # in the target repo
    source: str  # in the source repo
    ownership: str  # owned | seeded
    forge: str | None = None  # only for a target on this forge
    mode: str | None = None  # "755" or "644"; else the source file's git mode


@dataclass
class TargetFileSet:
    name: str
    gate: str | None
    files: list[TargetFile] = field(default_factory=list)


def _validator() -> Draft202012Validator:
    text = (
        resources.files("skillship")
        .joinpath("schemas/skillship.schema.json")
        .read_text("utf-8")
    )
    return Draft202012Validator(json.loads(text))


def load(source_root: Path) -> dict[str, Any]:
    """The settings, validated; {} when the source repo has none."""
    path = source_root / DISTROS_DIR / SETTINGS
    where = f"{DISTROS_DIR}/{SETTINGS}"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_bytes()) or {}
    except yaml.YAMLError as exc:
        raise SkillshipError(f"{where} is not valid YAML: {exc}") from exc
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [
            f"  {'/'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}"
            for e in errors
        ]
        raise SkillshipError(f"{where} is invalid:\n" + "\n".join(lines))
    return data


def forge_of(target: dict[str, Any]) -> str | None:
    """The forge of a `git` target: its `forge:`, else guessed from the URL's host."""
    if target.get("forge"):
        return target["forge"]
    url = target["url"]
    host = urlparse(url).hostname if "://" in url else url.partition(":")[0]
    host = (host or "").rpartition("@")[2].lower()
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if "gitlab" in host:
        return "gitlab"
    return None


def git_targets(profile: Profile) -> list[dict[str, Any]]:
    return [t for t in profile.targets if t["type"] == "git"]


def target_file_sets(repo: SourceRepo, profile: Profile) -> list[TargetFileSet]:
    """The target-file sets this distro delivers: none unless a `git` target gets a plugin shape.

    A file restricted to one forge is listed when some `git` target is on that forge.
    """
    settings = load(repo.root).get("target-files", {})
    opted = profile.target_files
    where = f"{DISTROS_DIR}/{profile.name}/distro.yaml"
    for name in opted:
        if name not in settings:
            raise SkillshipError(
                f"{where}: `target-files: {{{name}: ...}}` names no set of "
                f"{DISTROS_DIR}/{SETTINGS}"
            )
    targets = git_targets(profile)
    if profile.shape not in ("plugins", "plugin") or not targets:
        return []
    forges = {forge_of(t) for t in targets}
    out = []
    for name, spec in sorted(settings.items()):
        if opted.get(name, True) is False:
            continue
        files: list[TargetFile] = []
        for entry in spec["files"]:
            forge = (entry.get("when") or {}).get("forge")
            if forge and forge not in forges:
                continue
            files += _expand(repo, entry, forge)
        out.append(TargetFileSet(name, spec.get("gate"), sorted(files, key=_key)))
    return out


def _key(f: TargetFile) -> str:
    return f.path


def _expand(
    repo: SourceRepo, entry: dict[str, Any], forge: str | None
) -> list[TargetFile]:
    src, dest = entry["from"], entry["to"]
    where = f"{DISTROS_DIR}/{SETTINGS}"
    common = {
        "ownership": entry["ownership"],
        "forge": forge,
        "mode": entry.get("mode"),
    }
    if not src.endswith("/"):
        if dest.endswith("/"):
            raise SkillshipError(
                f"{where}: `to: {dest}` is a directory, `from: {src}` a file"
            )
        if not repo.is_tracked_file(src):
            raise SkillshipError(
                f"{where}: `from: {src}` is no file of the source repo"
            )
        return [TargetFile(dest, src, **common)]
    if not dest.endswith("/"):
        raise SkillshipError(
            f"{where}: `from: {src}` is a directory, so `to: {dest}` must end with `/`"
        )
    rename = entry.get("rename") or {}
    found = [f[len(src) :] for f in repo.tracked_files() if f.startswith(src)]
    if not found:
        raise SkillshipError(f"{where}: `from: {src}` holds no file of the source repo")
    out = []
    for rel in found:
        p = PurePosixPath(rel)
        name = rename.get(p.name, p.name)
        out.append(
            TargetFile(
                (PurePosixPath(dest) / p.parent / name).as_posix(), src + rel, **common
            )
        )
    return out
