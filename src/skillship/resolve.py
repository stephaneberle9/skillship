"""Resolve a profile against a source repo: which skills and files ship, and why.

The closure follows `metadata.requires` only. `metadata.suggests` is reported,
never followed. A delivered Markdown file that names a skill which does not
ship is a dangling mention; it blocks the lock unless the skill is suggested
by a shipped skill, the mention is phrased conditionally ("when installed"),
or the profile acknowledges it.

The delivered text is rewritten (see `rewrite`). In the `skills` and `plugin`
shapes that includes flattening, and a mention of a source plugin flattening
cannot rewrite blocks the lock unless the profile acknowledges that plugin.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from skillship import profile as profile_mod
from skillship.errors import SkillshipError
from skillship.layout import Layout
from skillship.profile import Profile
from skillship.rewrite import Rewrite, Rewriter, Rewritten
from skillship.source import SourceRepo

LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")

# Wells whose files describe the recipient's repo, not this one; mentions there are examples.
EXEMPLAR_WELLS = ("templates", "examples")

# How a document relies on a skill by name: `/skill`, `plugin:skill`, or
# "the `skill` skill". A bare backticked name is an illustration. The same
# rule as the plugin checks' DEPEND verdict, so both agree on what a mention is.
_MENTION = re.compile(r"`(/)?(?:([a-z0-9-]+):)?([a-z0-9-]+)`")
_CONDITIONAL = re.compile(
    r"\b(?:(?:when|if)\s+installed|(?:wenn|falls|sofern)\s+installiert)\b",
    re.IGNORECASE,
)
_PARAGRAPH = re.compile(r"(?:[^\n]*\S[^\n]*(?:\n|$))+")


@dataclass
class Dangling:
    ref: str
    at: str
    status: str  # acknowledged | suggested | conditional | blocking


@dataclass
class DeadLink:
    at: str
    target: str
    fix: str  # the profile edit or source change that makes it resolve


@dataclass
class Unflattened:
    plugin: str
    at: str
    status: str  # acknowledged | blocking


@dataclass
class PluginEntry:
    """A delivered plugin of a plugin shape."""

    name: str
    sources: list[str]  # the source plugins it is made of
    dependencies: list[Any]  # `plugin.json` entries
    description: str | None = None  # shape `plugin` only; else the source's


@dataclass
class Resolution:
    skills: dict[str, str] = field(
        default_factory=dict
    )  # id -> reason, in resolution order
    parent: dict[str, str] = field(
        default_factory=dict
    )  # id -> the skill that required it
    files: dict[str, list[str]] = field(default_factory=dict)
    extras: list[str] = field(
        default_factory=list
    )  # non-skill files that ship, repo-relative: LICENSE, MCP extras
    plugins: list[PluginEntry] = field(default_factory=list)
    generated: list[str] = field(
        default_factory=list
    )  # delivered paths skillship writes itself
    rewritten: dict[str, int] = field(
        default_factory=dict
    )  # repo path -> number of rewrites
    rewrites: dict[str, list[Rewrite]] = field(default_factory=dict)
    unflattened: list[Unflattened] = field(default_factory=list)
    suggested: dict[str, list[str]] = field(
        default_factory=dict
    )  # ref -> shipped skills suggesting it
    dangling: list[Dangling] = field(default_factory=list)
    dead_links: list[DeadLink] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Dangling]:
        return [d for d in self.dangling if d.status == "blocking"]

    @property
    def blocking_unflattened(self) -> list[Unflattened]:
        return [u for u in self.unflattened if u.status == "blocking"]


def resolve(repo: SourceRepo, profile: Profile) -> Resolution:
    res = Resolution()
    queue: deque[str] = deque()
    for pattern in profile.include:
        ids = repo.match(pattern)
        if not ids:
            res.errors.append(f"`include: {pattern}` matches no skill")
        for sid in ids:
            if sid not in res.skills:
                res.skills[sid] = f"include {pattern}"
                queue.append(sid)
    while queue:
        sid = queue.popleft()
        for dep in repo.skills[sid].requires:
            if dep not in res.skills:
                res.skills[dep] = f"required by {sid}"
                res.parent[dep] = sid
                queue.append(dep)

    for pattern in profile.exclude:
        ids = repo.match(pattern)
        if not ids:
            res.errors.append(f"`exclude: {pattern}` matches no skill")
        for sid in ids:
            if sid in res.skills:
                res.errors.append(f"`{sid}` is excluded, but ships: {_chain(res, sid)}")

    layout = Layout(profile.shape, PurePosixPath(profile.root), profile.plugin_name)
    if layout.shape in ("skills", "plugin"):
        by_name: dict[str, list[str]] = defaultdict(list)
        for sid in res.skills:
            by_name[repo.skills[sid].name].append(sid)
        for name, ids in sorted(by_name.items()):
            if len(ids) > 1:
                where = layout.skill_file(None, name, "").rstrip("/") + "/"
                res.errors.append(
                    f"shape `{layout.shape}` puts {', '.join(f'`{i}`' for i in sorted(ids))} "
                    f"into the same directory `{where}`"
                )

    for sid in res.skills:
        res.files[sid] = repo.files(repo.skills[sid], profile.file_excludes)
    if profile.license == "inherit":
        res.extras = [
            f
            for f in LICENSE_FILES
            if (repo.root / f).is_file() and not repo.is_symlink(f)
        ]
    sources = sorted({repo.skills[sid].plugin or "" for sid in res.skills})
    if profile.extras.get("mcp"):
        for plugin in sources:
            res.extras += repo.extras(plugin or None)
    _check_extras(repo, layout, res)

    if layout.is_plugin_shape:
        if "" in sources and not repo.home_plugin_name():
            res.errors.append(
                f"shape `{layout.shape}` needs every shipped skill in a plugin, but the "
                "root skills have no `.claude-plugin/plugin.json` naming one"
            )
        res.plugins = _plugins(repo, profile, layout, sources)
    res.generated = list(
        layout.generated([p.name for p in res.plugins], profile_mod.readme(profile))
    )

    for sid in res.skills:
        for ref in repo.skills[sid].suggests:
            if ref not in res.skills:
                res.suggested.setdefault(ref, []).append(sid)

    _rewrite(repo, profile, layout, res)
    res.dead_links = _dead_links(repo, layout, res)
    res.dangling = _dangling(repo, profile, res)
    acknowledged = {d.ref for d in res.dangling if d.status == "acknowledged"}
    acknowledged_plugins = {
        u.plugin for u in res.unflattened if u.status == "acknowledged"
    }
    for pattern in profile.acknowledge:
        if pattern in repo.plugin_names():
            if pattern not in acknowledged_plugins:
                res.errors.append(
                    f"`acknowledge: {pattern}` matches no unflattened mention -- remove it"
                )
        elif not set(repo.match(pattern)) & acknowledged:
            res.errors.append(
                f"`acknowledge: {pattern}` matches no dangling mention -- remove it"
            )
    return res


def closure(repo: SourceRepo, start: str) -> list[str]:
    """`start` and everything it requires, transitively, in resolution order."""
    seen, queue = [start], deque([start])
    while queue:
        for dep in repo.skills[queue.popleft()].requires:
            if dep not in seen:
                seen.append(dep)
                queue.append(dep)
    return seen


def _chain(res: Resolution, sid: str) -> str:
    links = [sid]
    while links[-1] in res.parent:
        links.append(res.parent[links[-1]])
    return " <- ".join(f"`{s}`" for s in links) + f" ({res.skills[links[-1]]})"


# -- extras and plugins ------------------------------------------------------


def extra_source(repo: SourceRepo, path: str) -> tuple[str | None, str]:
    """The source plugin (None for the root) an extra belongs to, and its path inside it."""
    parts = path.split("/")
    if len(parts) > 2 and parts[0] == "plugins" and parts[1] in repo.plugin_names():
        return parts[1], "/".join(parts[2:])
    return None, path


def extra_dest(repo: SourceRepo, layout: Layout, path: str) -> str:
    """Where an extra lands in the delivery."""
    if path in LICENSE_FILES:
        return layout.root_file(path)
    plugin, rel = extra_source(repo, path)
    return layout.plugin_file(plugin or repo.home_plugin_name(), rel)


def _check_extras(repo: SourceRepo, layout: Layout, res: Resolution) -> None:
    """Refuse two extras on one path, unless they agree or are `.mcp.json` files that merge."""
    by_dest: dict[str, list[str]] = defaultdict(list)
    for path in res.extras:
        try:
            by_dest[extra_dest(repo, layout, path)].append(path)
        except SkillshipError as exc:
            res.errors.append(str(exc))
            return
    for dest, paths in sorted(by_dest.items()):
        if len(paths) < 2:
            continue
        if dest.endswith(".mcp.json"):
            try:
                merge_mcp([(repo.root / p).read_bytes() for p in paths], paths)
            except SkillshipError as exc:
                res.errors.append(str(exc))
            continue
        contents = {(repo.root / p).read_bytes().replace(b"\r\n", b"\n") for p in paths}
        if len(contents) > 1:
            res.errors.append(
                f"shape `{layout.shape}` puts {', '.join(f'`{p}`' for p in paths)} on the "
                f"same path `{dest}` -- use shape `plugins`, or `plugin-extras.mcp: false`"
            )


def merge_mcp(contents: list[bytes], paths: list[str]) -> dict[str, Any]:
    """`.mcp.json` files merged: their servers side by side; one name with two configs fails."""
    servers: dict[str, Any] = {}
    owner: dict[str, str] = {}
    for data, path in zip(contents, paths):
        try:
            doc = json.loads(data)
        except ValueError as exc:
            raise SkillshipError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("mcpServers", {}), dict):
            raise SkillshipError(f"{path} has no `mcpServers` object")
        for name, config in doc.get("mcpServers", {}).items():
            if name in servers and servers[name] != config:
                raise SkillshipError(
                    f"`{owner[name]}` and `{path}` configure MCP server `{name}` "
                    "differently -- use shape `plugins`, or `plugin-extras.mcp: false`"
                )
            servers[name], owner[name] = config, path
    return {"mcpServers": dict(sorted(servers.items()))}


def _plugins(
    repo: SourceRepo, profile: Profile, layout: Layout, sources: list[str]
) -> list[PluginEntry]:
    """The delivered plugins, with `dependencies` trimmed to the plugins that ship.

    A dependency on an unshipped plugin of this repo is replaced by the shipped
    plugins it brings along, because Claude Code installs dependencies
    transitively and a skill may rely on that. A dependency on a plugin of
    another marketplace is kept: the recipient can install it from there.
    """
    market = repo.marketplace().get("name")
    root_name = repo.home_plugin_name()
    names = {p: (p or root_name or "") for p in sources}  # source -> plugin name
    local = {p: p for p in repo.plugin_names()}
    if root_name:
        local[root_name] = ""  # the root plugin, by name
    shipped = {names[p] for p in sources}

    def split(dep: Any) -> tuple[str, bool]:
        """A dependency's plugin name, and whether it is a plugin of this repo."""
        if isinstance(dep, dict):
            name, where = str(dep.get("name", "")), dep.get("marketplace")
        else:
            name, _, where = str(dep).partition("@")
        return name, name in local and (not where or where == market)

    def deps_of(name: str) -> list[Any]:
        found = repo.manifest(local[name] or None).get("dependencies", [])
        return list(found) if isinstance(found, list) else []

    def trimmed(name: str) -> list[Any]:
        out: list[Any] = []
        seen = {name}
        stack = list(reversed(deps_of(name)))
        while stack:
            dep = stack.pop()
            dep_name, ours = split(dep)
            if not ours:
                if dep not in out:
                    out.append(dep)
            elif dep_name not in seen:
                seen.add(dep_name)
                if dep_name in shipped:
                    out.append(dep_name)
                else:
                    stack += reversed(deps_of(dep_name))
        return out

    plugin_names = sorted(n for n in shipped if n)
    if layout.shape == "plugins":
        return [PluginEntry(n, [n], trimmed(n)) for n in plugin_names]
    outside: list[Any] = []
    for n in plugin_names:
        for dep in trimmed(n):
            if not split(dep)[1] and dep not in outside:
                outside.append(dep)
    assert layout.name
    return [PluginEntry(layout.name, plugin_names, outside, profile.plugin_description)]


# -- rewriting --------------------------------------------------------------


def rewriter(repo: SourceRepo, layout: Layout, shipped: list[str]) -> Rewriter:
    skills_of: dict[str, set[str]] = defaultdict(set)
    for s in repo.skills.values():
        if s.plugin:  # shared-material too: `requires` can name it
            skills_of[s.plugin].add(s.name)
    return Rewriter(dict(skills_of), set(shipped), layout.shape, layout.name)


def rewrite_file(
    rw: Rewriter, repo: SourceRepo, path: str, skill: str | None
) -> Rewritten | None:
    """The delivered copy of a text file; None for a binary one."""
    text = read_text(repo, path)
    if text is None:
        return None
    plugin = repo.skills[skill].plugin if skill else None
    return rw.rewrite(text, skill, plugin=plugin)


def _rewrite(
    repo: SourceRepo, profile: Profile, layout: Layout, res: Resolution
) -> None:
    rw = rewriter(repo, layout, list(res.skills))
    acknowledged = {p for p in profile.acknowledge if p in repo.plugin_names()}
    for path, label, skill in delivered_texts(repo, res.files, res.extras):
        out = rewrite_file(rw, repo, path, skill)
        if out is None:
            continue
        if out.count:
            res.rewritten[path] = out.count
            res.rewrites[path] = out.rewrites
        for plugin, line in out.unflattened:
            status = "acknowledged" if plugin in acknowledged else "blocking"
            res.unflattened.append(Unflattened(plugin, f"{label}:{line}", status))


def delivered_texts(
    repo: SourceRepo, files: dict[str, list[str]], extras: list[str]
) -> Iterator[tuple[str, str, str | None]]:
    """(repo path, label for reports, the skill of a SKILL.md) of every file to rewrite."""
    for sid, rels in files.items():
        base = repo.rel(repo.skills[sid].path)
        for rel in rels:
            yield f"{base}/{rel}", f"{sid}/{rel}", sid if rel == "SKILL.md" else None
    for path in extras:
        if path not in LICENSE_FILES and not path.endswith(".mcp.json"):
            yield path, path, None


def read_text(repo: SourceRepo, path: str) -> str | None:
    """A file's text with LF line endings; None for a binary file."""
    data = (repo.root / path).read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


# -- dead links --------------------------------------------------------------

_LINK = re.compile(r"\[[^\]]{0,120}\]\(([^)\s]+)\)")
# A link target with any of these is a placeholder or an illustration, not a path.
_PLACEHOLDER = re.compile(r"[<>\[\]{}]|\.\.\.|…")


def _dead_links(repo: SourceRepo, layout: Layout, res: Resolution) -> list[DeadLink]:
    """Relative links of delivered Markdown that resolve in the source repo but not in the delivery.

    A link that already resolves to nothing in the source is the plugin checks'
    `LINK` to report, or an illustration; either way not this delivery's doing.
    """
    delivered: dict[str, str] = {}  # repo path -> delivered path
    for sid, rels in res.files.items():
        skill = repo.skills[sid]
        home = skill.plugin or repo.home_plugin_name()
        base = repo.rel(skill.path)
        for rel in rels:
            try:
                delivered[f"{base}/{rel}"] = layout.skill_file(home, skill.name, rel)
            except SkillshipError:
                break  # a root skill without a plugin, already reported
    for path in res.extras:
        try:
            delivered[path] = extra_dest(repo, layout, path)
        except SkillshipError:
            pass  # reported by `_check_extras`
    present = set(delivered.values()) | set(res.generated)
    dirs = {str(PurePosixPath(d).parent) for d in present}
    while True:  # every ancestor directory
        more = {str(PurePosixPath(d).parent) for d in dirs} - dirs
        if not more:
            break
        dirs |= more

    out = []
    for src, dest in delivered.items():
        if not src.endswith(".md"):
            continue
        rel_parts = PurePosixPath(src).parts
        if any(w in rel_parts[:-1] for w in EXEMPLAR_WELLS):
            continue
        text = read_text(repo, src)
        if text is None:
            continue
        for m in _LINK.finditer(text):
            target = m.group(1)
            path = target.split("#")[0]
            if (
                not path
                or ":" in path
                or path.startswith("/")
                or _PLACEHOLDER.search(path)
            ):
                continue
            source_target = _normjoin(str(PurePosixPath(src).parent), path)
            if source_target is None or not (repo.root / source_target).exists():
                continue
            dest_target = _normjoin(str(PurePosixPath(dest).parent), path)
            if dest_target in present or dest_target in dirs:
                continue
            line = text.count("\n", 0, m.start()) + 1
            out.append(
                DeadLink(f"{src}:{line}", target, _link_fix(repo, source_target))
            )
    return out


def _normjoin(base: str, path: str) -> str | None:
    """`base/path` with `.` and `..` resolved; None when it climbs above the root."""
    parts: list[str] = []
    for part in [*PurePosixPath(base).parts, *path.split("/")]:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts) or "."


def _link_fix(repo: SourceRepo, target: str) -> str:
    for sid, skill in repo.skills.items():
        if (target + "/").startswith(repo.rel(skill.path) + "/"):
            return f"include: [{sid}]"
    _, rel = extra_source(repo, target)
    if rel in (".mcp.json", "CONNECTORS.md", "connectors") or rel.startswith(
        "connectors/"
    ):
        return "plugin-extras: {mcp: true}"
    if PurePosixPath(rel).name == "README.md":
        return "plugin-extras: {readme: true}, or reword the link in the source"
    return "reword the link in the source: the delivery has no such file"


# -- dangling mentions -------------------------------------------------------


def _dangling(repo: SourceRepo, profile: Profile, res: Resolution) -> list[Dangling]:
    acknowledged = {
        sid
        for p in profile.acknowledge
        if p not in repo.plugin_names()
        for sid in repo.match(p)
    }
    by_name: dict[str, list[str]] = defaultdict(list)
    for sid, s in repo.skills.items():
        if not s.is_shared:
            by_name[s.name].append(sid)
    out = []
    for sid in res.skills:
        skill = repo.skills[sid]
        for relpath in res.files[sid]:
            parts = relpath.split("/")
            if not relpath.endswith(".md") or any(
                w in parts[:-1] for w in EXEMPLAR_WELLS
            ):
                continue
            text = (
                (skill.path / relpath).read_text(encoding="utf-8").replace("\r\n", "\n")
            )
            for para in _PARAGRAPH.finditer(text):
                block = para.group(0)
                conditional = bool(_CONDITIONAL.search(block))
                for m in _MENTION.finditer(block):
                    slash, plugin, name = m.groups()
                    candidates = [
                        c
                        for c in by_name.get(name, [])
                        if not plugin or c.startswith(f"{plugin}:")
                    ]
                    if not candidates or any(c in res.skills for c in candidates):
                        continue
                    before = block[max(0, m.start() - 8) : m.start()]
                    after = block[m.end() : m.end() + 8]
                    if not (
                        slash
                        or plugin
                        or re.match(r"\s+skills?\b", after)
                        or re.search(r"\bskills?\s+$", before)
                    ):
                        continue
                    ref = candidates[0]
                    if ref in acknowledged:
                        status = "acknowledged"
                    elif ref in res.suggested:
                        status = "suggested"
                    elif conditional:
                        status = "conditional"
                    else:
                        status = "blocking"
                    line = text[: para.start() + m.start()].count("\n") + 1
                    out.append(Dangling(ref, f"{sid}/{relpath}:{line}", status))
    return out


def check(res: Resolution) -> None:
    """Raise when the resolution cannot be locked."""
    problems = list(res.errors)
    for d in res.blocking:
        problems.append(f"{d.at}: names `{d.ref}`, which does not ship")
    for d in res.dead_links:
        problems.append(
            f"{d.at}: links `{d.target}`, which the delivery does not contain -- {d.fix}"
        )
    for u in res.blocking_unflattened:
        problems.append(
            f"{u.at}: names plugin `{u.plugin}`, which flattening cannot rewrite"
        )
    if problems:
        raise SkillshipError("cannot lock:\n" + "\n".join(f"  {p}" for p in problems))
