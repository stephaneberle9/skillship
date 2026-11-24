"""A distro's profile, `distros/<name>/distro.yaml`: read, validated, defaults filled in."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from skillship.errors import NotImplementedYet, SkillshipError
from skillship.layout import Layout

DISTROS_DIR = "distros"
PROFILE = "distro.yaml"
LOCK = "distro.lock.yaml"

# What this version can resolve and build; everything else in the schema is
# accepted, so a profile written for a later version stays valid, and refused
# with exit status 2 when it is used.
BUILD_TARGETS_IMPLEMENTED = ("archive", "agensi")
DELIVER_TARGETS_IMPLEMENTED = (*BUILD_TARGETS_IMPLEMENTED, "git")

# The recipient of a profile that names none. A zip is handed over by whatever
# channel is at hand, so it needs no named recipient; what it holds says where
# it is installed. A git target and an Agensi pack need one named.
HANDOVER = "Early adopters, one-off hand-overs by email, Slack, WhatsApp and the like"
CLAUDE_CODE = "Claude Code"  # copies skills, adds a marketplace
# take a plugin as an upload, and a zip of skills as one too
UPLOADS = "Claude Desktop and claude.ai"
# Where an archive of each shape is installed; a target without a layout
# has shape `skills`.
_CLIENTS = {
    "plugin": (UPLOADS,),
    "marketplace": (CLAUDE_CODE,),
}
_SKILLS_CLIENTS = (CLAUDE_CODE, UPLOADS)

# The keys a target's `layout` takes besides `shape` and `root`, per shape.
_LAYOUT_KEYS = {
    "skills": (),
    "plugin": ("name", "description"),
    "marketplace": ("marketplace", "flatten", "plugin-name", "plugin-description"),
}


def default_recipient(targets: list[dict[str, Any]]) -> str | None:
    """The recipient of a profile without one, or None when its targets need one named."""
    clients: set[str] = set()
    for target in targets:
        if target.get("type") != "archive":
            return None
        layout = target.get("layout")
        shape = layout.get("shape") if isinstance(layout, dict) else None
        clients.update(_CLIENTS.get(shape, _SKILLS_CLIENTS))
    if not clients:
        return None
    # Claude Code first: "Claude Code, Claude Desktop and claude.ai"
    return f"{HANDOVER}, for {', '.join(sorted(clients))}"


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
    license: str = "inherit"
    # as written, plus `layout`: the target's `Layout`, defaults filled in
    targets: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, bool] = field(default_factory=dict)
    target_files: dict[str, bool] = field(default_factory=dict)  # set -> delivered
    version: str = "bump-patch"  # keep | bump-patch, for plugins the recipient versions
    auto: bool = False  # delivered by CI on every push to the default branch
    # layout -> its directory under `staging/`, over all targets of the profile
    slugs: dict[Layout, str] = field(default_factory=dict)


def layouts(profile: Profile) -> list[tuple[Layout, list[dict[str, Any]]]]:
    """The distinct layouts of the profile's targets, each with its targets, in profile order."""
    out: dict[Layout, list[dict[str, Any]]] = {}
    for target in profile.targets:
        out.setdefault(target["layout"], []).append(target)
    # without a target there is still something to check and stage: the skills as they are
    return list(out.items()) or [(Layout("skills"), [])]


def archive_name(profile: Profile, target: dict[str, Any]) -> str:
    """The file name of an `archive` target, without `.zip`."""
    return target.get("name") or f"{profile.name}-{target['layout'].shape}"


def target_label(profile: Profile, target: dict[str, Any]) -> str:
    """What the lock and messages call a target: the file it writes, or where it pushes."""
    kind = target["type"]
    if kind == "archive":
        return f"{archive_name(profile, target)}.zip"
    if kind == "agensi":
        return f"agensi/{target.get('name', profile.name)}.zip"
    return str(target.get("url", kind))


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


def _hints(data: Any) -> list[str]:
    """What a profile written for an earlier release has to change."""
    if not isinstance(data, dict):
        return []
    out = []
    if "layout" in data:
        out.append(
            "  `layout` now belongs to a target: move it under each `archive` or "
            "`git` target"
        )
    if "upload" in data:
        out.append("  `upload.inline-shared` went away with the `skill-upload` target")
    targets = data.get("targets")
    for target in targets if isinstance(targets, list) else []:
        if not isinstance(target, dict):
            continue
        kind = target.get("type")
        if kind == "plugin-upload":
            out.append(
                "  a `plugin-upload` target is now "
                "`{type: archive, layout: {shape: plugin}}`"
            )
        elif kind == "skill-upload":
            out.append(
                "  the `skill-upload` target is gone: Claude Desktop and claude.ai "
                "take plugins, so use `{type: archive, layout: {shape: plugin}}`"
            )
        layout = target.get("layout")
        if isinstance(layout, dict) and "merged-plugin" in layout:
            out.append(
                "  `merged-plugin` is now `flatten: true` with `plugin-name` and "
                "`plugin-description`, or shape `plugin` with `name` and `description`"
            )
    return list(dict.fromkeys(out))


def _layout(name: str, target: dict[str, Any]) -> Layout | str:
    """A target's layout, defaults filled in; a message when its keys do not fit its shape."""
    if target["type"] == "agensi":
        return Layout("skills")  # a pack holds flat `skills/<name>/` folders
    raw = target.get("layout", {})
    shape = raw.get("shape", "skills")
    extra = [k for k in raw if k not in ("shape", "root", *_LAYOUT_KEYS[shape])]
    if extra:
        return f"`{'`, `'.join(extra)}` does not apply to shape `{shape}`"
    root = PurePosixPath(raw.get("root", "."))
    if shape == "skills":
        return Layout(shape, root)
    if shape == "plugin":
        return Layout(shape, root, raw.get("name", name), raw.get("description"))
    market = raw.get("marketplace", name)
    if not raw.get("flatten"):
        named = [k for k in ("plugin-name", "plugin-description") if k in raw]
        if named:
            return f"`{'`, `'.join(named)}` needs `flatten: true`"
        return Layout(shape, root, marketplace=market)
    return Layout(
        shape, root, raw.get("plugin-name", name), raw.get("plugin-description"), market
    )


def _slugs(targets: list[dict[str, Any]]) -> dict[Layout, str]:
    out: dict[Layout, str] = {}
    for target in targets:
        layout = target["layout"]
        if layout in out:
            continue
        taken = set(out.values())
        slug, n = layout.shape, 1
        while slug in taken:
            n += 1
            slug = f"{layout.shape}-{n}"
        out[layout] = slug
    return out


def load(source_root: Path, name: str) -> Profile:
    path = source_root / DISTROS_DIR / name / PROFILE
    where = f"{DISTROS_DIR}/{name}/{PROFILE}"
    if not path.is_file():
        raise SkillshipError(f"no profile at {where}")
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillshipError(f"{where} is not valid YAML: {exc}") from exc
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [
            f"  {'/'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}"
            for e in errors
        ]
        raise SkillshipError(f"{where} is invalid:\n" + "\n".join(lines + _hints(data)))
    targets = [dict(t) for t in data.get("targets", [])]
    recipient = data.get("recipient")
    if recipient is None:
        recipient = default_recipient(targets)
        if recipient is None:
            raise SkillshipError(
                f"{where} is invalid:\n"
                "  (top level): `recipient` is required unless every target is an "
                "`archive`"
            )
    problems = []
    for i, target in enumerate(targets):
        layout = _layout(name, target)
        if isinstance(layout, str):
            problems.append(f"  targets/{i}/layout: {layout}")
        else:
            target["layout"] = layout
    if problems:
        raise SkillshipError(f"{where} is invalid:\n" + "\n".join(problems))
    profile = Profile(
        name=name,
        path=path,
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        recipient=recipient,
        include=data["include"],
        exclude=data.get("exclude", []),
        acknowledge=data.get("acknowledge", []),
        file_excludes=data.get("files", {}).get("exclude", []),
        license=data.get("license", "inherit"),
        targets=targets,
        extras=data.get("plugin-extras", {}),
        target_files=data.get("target-files", {}),
        version=data.get("version", "bump-patch"),
        auto=data.get("auto", False),
        slugs=_slugs(targets),
    )
    written: dict[str, int] = {}
    for i, target in enumerate(targets):
        if target["type"] not in ("archive", "agensi"):
            continue
        label = target_label(profile, target)
        if label in written:
            raise SkillshipError(
                f"{where} is invalid:\n"
                f"  targets/{i}: writes `{label}`, like targets/{written[label]} -- "
                "give one of them a `name`"
            )
        written[label] = i
    return profile


def readme(profile: Profile, layout: Layout) -> bool:
    """Whether a layout gets a generated README: always where it holds plugins."""
    where = f"{DISTROS_DIR}/{profile.name}/{PROFILE}"
    if layout.has_plugins:
        if profile.extras.get("readme") is False:
            raise SkillshipError(
                f"{where}: shape `{layout.shape}` always generates a README per plugin, "
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
    for target in profile.targets:
        if target["type"] == "git" and target["layout"].shape == "plugin":
            raise NotImplementedYet(
                f"{where}: shape `plugin` in a `git` target is not implemented yet; "
                "use a marketplace with `flatten: true`"
            )
