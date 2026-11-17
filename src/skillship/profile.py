"""A distro's profile, `distros/<name>/distro.yaml`: read, validated, defaults filled in."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from skillship.errors import NotImplementedYet, SkillshipError

DISTROS_DIR = "distros"
PROFILE = "distro.yaml"
LOCK = "distro.lock.yaml"

# What this version can resolve and build; everything else in the schema is
# accepted, so a profile written for a later version stays valid, and refused
# with exit status 2 when it is used.
BUILD_TARGETS_IMPLEMENTED = ("archive", "skill-upload", "plugin-upload", "agensi")
DELIVER_TARGETS_IMPLEMENTED = (*BUILD_TARGETS_IMPLEMENTED, "git")


@dataclass
class Profile:
    name: str
    path: Path
    digest: str
    recipient: str
    include: list[str]
    exclude: list[str] = field(default_factory=list)
    acknowledge: list[str] = field(default_factory=list)
    file_excludes: list[str] = field(default_factory=list)
    shape: str = "skills"
    root: str = "."
    plugin_name: str | None = None  # the merged plugin, shape `plugin`
    plugin_description: str | None = None
    marketplace: str | None = None
    license: str = "inherit"
    targets: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, bool] = field(default_factory=dict)
    target_files: dict[str, bool] = field(default_factory=dict)  # set -> delivered
    version: str = "bump-patch"  # keep | bump-patch, for plugins the recipient versions
    inline_shared: bool = False  # upload.inline-shared


def _validator() -> Draft202012Validator:
    text = (
        resources.files("skillship")
        .joinpath("schemas/distro.schema.json")
        .read_text("utf-8")
    )
    return Draft202012Validator(json.loads(text))


def distro_names(source_root: Path) -> list[str]:
    base = source_root / DISTROS_DIR
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if (d / PROFILE).is_file())


def load(source_root: Path, name: str) -> Profile:
    path = source_root / DISTROS_DIR / name / PROFILE
    if not path.is_file():
        raise SkillshipError(f"no profile at {DISTROS_DIR}/{name}/{PROFILE}")
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillshipError(
            f"{DISTROS_DIR}/{name}/{PROFILE} is not valid YAML: {exc}"
        ) from exc
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [
            f"  {'/'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}"
            for e in errors
        ]
        raise SkillshipError(
            f"{DISTROS_DIR}/{name}/{PROFILE} is invalid:\n" + "\n".join(lines)
        )
    layout = data.get("layout", {})
    return Profile(
        name=name,
        path=path,
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        recipient=data["recipient"],
        include=data["include"],
        exclude=data.get("exclude", []),
        acknowledge=data.get("acknowledge", []),
        file_excludes=data.get("files", {}).get("exclude", []),
        shape=layout.get("shape", "skills"),
        root=layout.get("root", "."),
        plugin_name=layout.get("name"),
        plugin_description=layout.get("description"),
        marketplace=layout.get("marketplace"),
        license=data.get("license", "inherit"),
        targets=data.get("targets", []),
        extras=data.get("plugin-extras", {}),
        target_files=data.get("target-files", {}),
        version=data.get("version", "bump-patch"),
        inline_shared=data.get("upload", {}).get("inline-shared", False),
    )


def readme(profile: Profile) -> bool:
    """Whether the delivery gets a generated README: always in the plugin shapes."""
    where = f"{DISTROS_DIR}/{profile.name}/{PROFILE}"
    if profile.shape in ("plugins", "plugin"):
        if profile.extras.get("readme") is False:
            raise SkillshipError(
                f"{where}: shape `{profile.shape}` always generates a README per plugin, "
                "because the source README lists skills that do not ship -- remove "
                "`plugin-extras.readme: false`"
            )
        return True
    return bool(profile.extras.get("readme", False))


def require_implemented(profile: Profile) -> None:
    """Refuse, with exit status 2, a profile that needs what this version cannot resolve yet."""
    where = f"{DISTROS_DIR}/{profile.name}/{PROFILE}"
    if profile.license not in ("inherit", "none"):
        raise NotImplementedYet(
            f"{where}: `license: {profile.license}` is not implemented yet; use `inherit` or `none`"
        )
