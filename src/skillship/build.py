"""Build a distro from its lock: the staging tree, `.skillship.json` and the archives.

An `archive` target zips the whole tree; the upload targets (see `upload`)
write one `.skill` per skill or one zip per plugin, from the same delivered
files, under `<out>/<distro>/<target type>/`.

The zip is deterministic: entries sorted, one fixed timestamp, Unix permissions
from git, and text files with LF line endings. Two builds of the same lock from
the same source commit produce the same bytes.

Besides the skills and extras the lock lists, build writes the files the lock
lists under `generated`: in the plugin shapes a `plugin.json` and a README per
delivered plugin and the `marketplace.json`, in the `skills` shape a README
when the profile asks for one. Every delivered text file is rewritten as the
lock counts (see `rewrite`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from skillship import preflight, upload
from skillship.errors import NotImplementedYet, SkillshipError
from skillship.layout import Layout, check_dest
from skillship.profile import BUILD_TARGETS_IMPLEMENTED, Profile
from skillship.resolve import (
    LICENSE_FILES,
    extra_dest,
    extra_source,
    merge_mcp,
    rewrite_file,
    rewriter,
)
from skillship.source import _FRONTMATTER, SourceRepo

RECORD = ".skillship.json"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Targets `build` has nothing to do for: `deliver` handles them.
DELIVER_ONLY = ("git",)

# Keys of a source `plugin.json` a delivered plugin does not keep: the repository
# and homepage are the source's, component paths may name files that do not
# ship, and `dependencies` and `license` are set from the lock and the profile.
_DROPPED_KEYS = (
    "repository", "homepage", "commands", "agents", "skills", "hooks",
    "mcpServers", "outputStyles", "lspServers", "dependencies", "license",
)  # fmt: skip


@dataclass
class BuildResult:
    staging: Path
    archives: list[Path]
    source: str
    dirty: bool
    preflights: list[tuple[Path, preflight.Report]] = field(default_factory=list)


@dataclass
class DeliveredFile:
    data: bytes
    mode: int
    ownership: str  # owned | merged | seeded
    source: str | None = (
        None  # the source repo file it is made from; None when generated
    )
    # a seeded target file: the source files joined into it (`source` names them
    # with ` + `), and the file to announce when the target holds its own
    parts: tuple[str, ...] = ()
    announce: str | None = None


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

    contents = delivered(repo, profile, lock)
    sha, dirty = repo.head()
    source = f"{repo.name()}@{sha}" if sha else repo.name()

    base = out / profile.name
    staging = base / "staging"
    for path in (out, base, staging, *(base / k for k in upload.UPLOAD_TARGETS)):
        if path.is_symlink():
            raise SkillshipError(
                f"{path} is a symlink; build writes only into a real directory"
            )
    if staging.exists():
        shutil.rmtree(staging)
    record: dict[str, Any] = {
        "distro": profile.name,
        "source": source,
        "lock-digest": lock_digest,
        "files": {
            dest: {
                "ownership": f.ownership,
                "sha256": hashlib.sha256(f.data).hexdigest(),
            }
            for dest, f in sorted(contents.items())
        },
    }
    if dirty:
        record["dirty"] = True
    contents[RECORD] = DeliveredFile(
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(), 0o644, ""
    )

    for dest, f in contents.items():
        path = staging / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f.data)

    files = {d: (f.data, f.mode) for d, f in contents.items()}
    archives = []
    preflights = []
    for target in profile.targets:
        kind = target["type"]
        if kind == "archive":
            archive = base / f"{target.get('name', profile.name)}.zip"
            _zip(archive, files)
            archives.append(archive)
        elif kind == upload.SKILL_UPLOAD:
            layout = Layout.from_lock(lock)
            for name, skill in sorted(
                upload.skill_archives(repo, layout, lock, files).items()
            ):
                archive = base / kind / f"{name}.skill"
                _zip(archive, skill)
                archives.append(archive)
        elif kind == upload.PLUGIN_UPLOAD:
            layout = Layout.from_lock(lock)
            for name, plugin in sorted(
                upload.plugin_archives(layout, lock, files).items()
            ):
                archive = base / kind / f"{name}.zip"
                _zip(archive, plugin)
                archives.append(archive)
        elif kind == upload.AGENSI:
            layout = Layout.from_lock(lock)
            name = target.get("name", profile.name)
            title = target.get("title", profile.name)
            package = upload.agensi_archive(repo, layout, lock, files, title)
            archive = base / kind / f"{name}.zip"
            _zip(archive, package)
            archives.append(archive)
            report = preflight.scan(package)
            path = base / kind / f"{name}.preflight.md"
            _atomic(path, preflight.render(report, name).encode("utf-8"))
            preflights.append((path, report))
    return BuildResult(staging, archives, source, dirty, preflights)


def delivered(
    repo: SourceRepo, profile: Profile, lock: dict[str, Any]
) -> dict[str, DeliveredFile]:
    """Delivered path -> content, for every file of the delivery but `.skillship.json`."""
    layout = Layout.from_lock(lock)
    shipped = [entry["id"] for entry in lock.get("skills", [])]
    rw = rewriter(repo, layout, shipped)
    out: dict[str, DeliveredFile] = {}
    listed: dict[str | None, list[tuple[str, str]]] = {}  # plugin -> (skill, SKILL.md)

    def put(
        dest: str,
        data: bytes,
        mode: int,
        ownership: str = "owned",
        source: str | None = None,
    ) -> None:
        check_dest(dest)
        if dest in out or dest == RECORD:
            raise SkillshipError(f"two files of the lock land on `{dest}`")
        out[dest] = DeliveredFile(data, mode, ownership, source)

    def content(src: str, skill: str | None) -> bytes:
        out = rewrite_file(rw, repo, src, skill)
        if out is None:  # binary, or text in another encoding: not rewritten
            return normalized((repo.root / src).read_bytes())
        return out.text.encode("utf-8")

    for entry in lock.get("skills", []):
        skill = repo.skills.get(entry["id"])
        if skill is None:
            raise SkillshipError(
                f"the lock ships `{entry['id']}`, which is no skill of the source repo"
            )
        home = skill.plugin or repo.home_plugin_name()
        for rel in entry["files"]:
            src = f"{repo.rel(skill.path)}/{rel}"
            if ".." in PurePosixPath(rel).parts or repo.is_symlink(src):
                raise SkillshipError(
                    f"the lock ships `{src}`, which is not a plain file of the skill"
                )
            if not (repo.root / src).is_file():
                raise SkillshipError(f"the lock ships `{src}`, which does not exist")
            dest = layout.skill_file(home, skill.name, rel)
            sid = entry["id"] if rel == "SKILL.md" else None
            put(dest, content(src, sid), repo.mode(src), source=src)
        if not skill.is_shared:
            listed.setdefault(layout.plugin_name(home), []).append(
                (skill.name, layout.skill_file(home, skill.name, "SKILL.md"))
            )

    mcp: dict[str, list[str]] = {}
    for extra in lock.get("extras", []):
        _check_extra(repo, extra)
        dest = extra_dest(repo, layout, extra)
        if dest.endswith(".mcp.json"):
            mcp.setdefault(dest, []).append(extra)
        elif (
            dest not in out
        ):  # two plugins' identical copies; `lock` refuses differing ones
            put(dest, content(extra, None), repo.mode(extra), source=extra)
    for dest, paths in mcp.items():
        merged = merge_mcp([(repo.root / p).read_bytes() for p in paths], paths)
        put(dest, json_bytes(merged), 0o644)

    plugins = {p["name"]: p for p in lock.get("plugins", [])}
    expected = layout.generated(list(plugins), readme=True)
    for dest in lock.get("generated", []):
        if dest not in expected:
            raise SkillshipError(
                f"the lock generates `{dest}`, which skillship does not write"
            )
        kind, plugin = expected[dest]
        if kind == "marketplace":
            market = _marketplace(repo, layout, lock, plugins)
            put(dest, json_bytes(market), 0o644, "merged")
        elif kind == "manifest":
            assert plugin
            manifest = _manifest(repo, profile, layout, plugins[plugin])
            put(dest, json_bytes(manifest), 0o644)
        else:
            entry = plugins.get(plugin) if plugin else None
            readme = _readme(
                repo, layout, plugin, entry, sorted(listed.get(plugin, [])), out
            )
            put(dest, readme.encode("utf-8"), 0o644)
    return out


def _check_extra(repo: SourceRepo, extra: str) -> None:
    _, rel = extra_source(repo, extra)
    ok = (
        extra in LICENSE_FILES
        or rel in (".mcp.json", "CONNECTORS.md")
        or (rel.startswith("connectors/") and ".." not in PurePosixPath(rel).parts)
    )
    path = repo.root / extra
    if (
        not ok
        or repo.is_symlink(extra)
        or not path.is_file()
        or not path.resolve().is_relative_to(repo.root.resolve())
    ):
        raise SkillshipError(
            f"the lock ships `{extra}`, which is no license file or MCP extra of the source repo"
        )


def _sources(repo: SourceRepo, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The `plugin.json` of each source plugin a delivered plugin is made of."""
    names = entry.get("from", [entry["name"]])
    return [repo.manifest(n if n in repo.plugin_names() else None) for n in names]


def _manifest(
    repo: SourceRepo, profile: Profile, layout: Layout, entry: dict[str, Any]
) -> dict[str, Any]:
    manifests = _sources(repo, entry)
    if layout.shape == "plugins":
        out = {k: v for k, v in manifests[0].items() if k not in _DROPPED_KEYS}
        out["name"] = entry["name"]
    else:
        out = {"name": entry["name"]}
        if entry.get("description"):
            out["description"] = entry["description"]
        authors = [m.get("author") for m in manifests]
        if authors[0] and all(a == authors[0] for a in authors):
            out["author"] = authors[0]
    licenses = [m.get("license") for m in manifests]
    if (
        profile.license == "inherit"
        and licenses[0]
        and all(lic == licenses[0] for lic in licenses)
    ):
        out["license"] = licenses[0]
    if entry.get("dependencies"):
        out["dependencies"] = entry["dependencies"]
    return out


def _description(repo: SourceRepo, layout: Layout, entry: dict[str, Any]) -> str | None:
    if layout.shape == "plugin":
        return entry.get("description")
    value = _sources(repo, entry)[0].get("description")
    return value if isinstance(value, str) else None


def _marketplace(
    repo: SourceRepo,
    layout: Layout,
    lock: dict[str, Any],
    plugins: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entries = []
    for name in sorted(plugins):
        item: dict[str, Any] = {"name": name}
        description = _description(repo, layout, plugins[name])
        if description:
            item["description"] = description
        item["source"] = f"./plugins/{name}"
        entries.append(item)
    owner = repo.marketplace().get("owner") or {"name": repo.name()}
    return {"name": lock["layout"]["marketplace"], "owner": owner, "plugins": entries}


def _readme(
    repo: SourceRepo,
    layout: Layout,
    plugin: str | None,
    entry: dict[str, Any] | None,
    skills: list[tuple[str, str]],
    out: dict[str, DeliveredFile],
) -> str:
    """A README listing the delivered skills, linked to their SKILL.md."""
    base = layout.plugin_dir(plugin) if plugin else layout.root
    lines = [f"# {plugin or 'Skills'}", ""]
    description = _description(repo, layout, entry) if entry else None
    if description:
        lines += [description, ""]
    rows: dict[bool, list[str]] = {True: [], False: []}
    for name, skill_md in skills:
        meta = _frontmatter(out[skill_md].data)
        invocable = meta.get("user-invocable", True) is not False
        link = PurePosixPath(skill_md).relative_to(base).as_posix()
        label = f"/{name}" if invocable else name
        text = " ".join(str(meta.get("description", "")).split()).replace("|", "\\|")
        rows[invocable].append(f"| [`{label}`]({link}) | {text} |")
    header = ["| Skill | Description |", "| --- | --- |"]
    if rows[True]:
        lines += ["## Skills", "", *header, *rows[True], ""]
    if rows[False]:
        lines += [
            "## Background Skills",
            "",
            "Other skills load these; invoking them as slash commands has no effect.",
            "",
            *header,
            *rows[False],
            "",
        ]
    deps = (entry or {}).get("dependencies") or []
    if deps:
        lines += [
            "## Dependencies",
            "",
            "Installed automatically with this plugin:",
            "",
        ]
        for dep in deps:
            if isinstance(dep, str) and "@" not in dep:
                lines.append(f"- [`{dep}`](../{dep}/README.md)")
            else:
                name = dep.get("name") if isinstance(dep, dict) else dep
                lines.append(f"- `{name}`")
        lines.append("")
    return "\n".join(lines)


def _frontmatter(data: bytes) -> dict[str, Any]:
    m = _FRONTMATTER.match(data.decode("utf-8", errors="replace"))
    if not m:
        return {}
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def normalized(data: bytes) -> bytes:
    """Text with LF line endings; binary (anything with a NUL byte) unchanged."""
    if b"\0" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _zip(path: Path, contents: dict[str, tuple[bytes, int]]) -> None:
    _atomic(path, contents)


def _atomic(path: Path, data: bytes | dict[str, tuple[bytes, int]]) -> None:
    """Write `data`, or a zip of it, to `path` without following a symlink there.

    Written to a fresh file and moved into place: `os.replace` swaps the
    directory entry, so a symlink planted at `path` is replaced, never followed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise SkillshipError(
            f"{path.parent} is a symlink; build writes only into a real directory"
        )
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            if isinstance(data, bytes):
                fh.write(data)
            else:
                _write_zip(fh, data)
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
