"""What is uploaded: an archive of shape `plugin`, and the `agensi` target.

An archive of shape `plugin` is one plugin with `.claude-plugin/plugin.json` at
the top, for the plugin upload of Claude Desktop and claude.ai. Its skills keep
their frontmatter: plugins read the keys Claude Code reads.

An upload is installed on its own, so a path that leaves it no longer
resolves: a path out of the plugin, or out of a pack skill. The lock lists
every such path that resolves in the source.

`agensi` writes one Agensi skill pack per distro, for the Agensi marketplace: a
`README.md` whose heading names the pack, `skills/<name>/` per skill, and the
shipped `shared-material/` as `shared/`, with the paths into it rewritten to
`../../shared/...`. A distro of one skill is a single skill instead, its files
at the top, shared material in its own `shared/`. It is built from the `skills`
layout, so the delivered text is flattened. A pack skill sees its own folder
and `shared/` only, so paths to other skills are listed. The lock checks
Agensi's limits for an upload from an agent; `preflight` mirrors its security
scan.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from skillship.errors import SkillshipError
from skillship.layout import Layout
from skillship.source import _FRONTMATTER, SHARED_DIR, SourceRepo

AGENSI = "agensi"
PLUGIN = "plugin"  # an archive of shape `plugin`, in the lock's `uploads`

# The frontmatter keys claude.ai, Claude Desktop and the Skills API accept.
ALLOWED_KEYS = (
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
)  # fmt: skip
DESCRIPTION_LIMIT = 1024
# What Agensi reads besides those: the listing fields its upload form fills in.
AGENSI_KEYS = (
    *ALLOWED_KEYS, "summary", "version", "tags", "keywords", "use_cases", "docs_url",
)  # fmt: skip

# Agensi's limits for an upload from an agent, the route the skillship skill takes.
AGENSI_MAX_FILES = 100
AGENSI_MAX_BYTES = 5 * 1024 * 1024
AGENSI_MAX_SKILLS = 50
AGENSI_SHARED = "shared"
EXECUTABLES = (
    ".exe", ".dll", ".so", ".dylib", ".bin", ".app", ".dmg", ".msi", ".deb", ".rpm",
    ".wasm", ".class", ".jar", ".war",
)  # fmt: skip
BUNDLED = ("node_modules", "site-packages", ".venv", "venv")

# Wells whose files describe the recipient's repo; their paths are examples.
_EXEMPLAR_WELLS = ("templates", "examples")

_TOP_KEY = re.compile(r"([A-Za-z0-9_-]+)[ \t]*:(?:[ \t]|\n|$)")
_PATH_CHARS = r"[^\s)\]`'\"<>|*,;]*"
# `../x`, also after `<skill-dir>/` or `${CLAUDE_SKILL_DIR}/`, which anchor it at the skill.
_UP = re.compile(
    r"(?<![\w./-])(?P<pre><skill-dir>/|\$\{CLAUDE_SKILL_DIR\}/)?"
    rf"(?P<path>(?:\.\./)+{_PATH_CHARS})"
)
# `skills/<name>/...`, a path from the plugin's root.
_SKILLS = re.compile(
    rf"(?<![\w./-])(?P<path>skills/[a-z0-9]+(?:-[a-z0-9]+)*/{_PATH_CHARS})"
)


@dataclass
class PathRef:
    line: int
    start: int  # of the path in the text, after any `<skill-dir>/`
    end: int
    path: str  # as written
    target: str  # repo-relative, normalized
    anchored: bool  # resolved from the skill's directory, not the file's


@dataclass
class Unresolved:
    target: str  # plugin | agensi
    at: str
    path: str


@dataclass
class Uploads:
    # target -> skill id -> keys
    dropped: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    unresolved: list[Unresolved] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.dropped or self.unresolved)


# -- frontmatter -------------------------------------------------------------


def frontmatter(text: str) -> dict[str, Any]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def sanitize(
    text: str, allowed: tuple[str, ...] = ALLOWED_KEYS
) -> tuple[str, list[str]]:
    """The text with every top-level frontmatter key outside `allowed` removed, and those keys.

    The edit is textual, so the kept keys keep their formatting; it is checked by
    parsing both versions, and refused when the result is not the same mapping
    minus the dropped keys.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return text, []
    block = m.group(1)
    kept, dropped, skipping = [], [], False
    for line in block.splitlines(keepends=True):
        key = _TOP_KEY.match(line)
        if key:
            skipping = key.group(1) not in allowed
            if skipping:
                dropped.append(key.group(1))
                continue
        elif skipping and (
            line[:1] in (" ", "\t", "-") or not line.strip()
        ):  # the dropped key's value
            continue
        else:
            skipping = False
        kept.append(line)
    if not dropped:
        return text, []
    new = "".join(kept)
    try:
        before = yaml.safe_load(block) or {}
        after = yaml.safe_load(new) or {}
    except yaml.YAMLError:
        after, before = None, {}
    if after != {k: v for k, v in before.items() if k in allowed}:
        raise SkillshipError(
            f"cannot drop the frontmatter keys {', '.join(dropped)} without changing the "
            "others -- simplify their formatting in the source"
        )
    return text[: m.start(1)] + new + text[m.end(1) :], dropped


def spec_errors(sid: str, meta: dict[str, Any]) -> list[str]:
    """What an upload refuses in a skill's frontmatter."""
    out = []
    description = meta.get("description")
    if isinstance(description, str) and len(description) > DESCRIPTION_LIMIT:
        out.append(
            f"`{sid}`: the description has {len(description)} characters; "
            f"uploads take at most {DESCRIPTION_LIMIT}"
        )
    metadata = meta.get("metadata")
    if metadata is not None and (
        not isinstance(metadata, dict)
        or not all(isinstance(v, str) for v in metadata.values())
    ):
        out.append(f"`{sid}`: `metadata` must map keys to strings for an upload")
    return out


# -- paths that leave an upload ----------------------------------------------


def path_refs(text: str, file: str, skill_dir: str, plugin_dir: str) -> list[PathRef]:
    """Relative paths in `text`, a file at repo path `file`, resolved against the repo."""
    out = []
    for m in _UP.finditer(text):
        anchored = bool(m.group("pre"))
        base = skill_dir if anchored else posixpath.dirname(file)
        target = _join(base, m.group("path"))
        if target is not None:
            line = text.count("\n", 0, m.start()) + 1
            out.append(
                PathRef(
                    line,
                    m.start("path"),
                    m.end("path"),
                    m.group("path"),
                    target,
                    anchored,
                )
            )
    for m in _SKILLS.finditer(text):
        target = _join(plugin_dir, m.group("path"))
        if target is not None:
            line = text.count("\n", 0, m.start()) + 1
            out.append(
                PathRef(
                    line, m.start("path"), m.end("path"), m.group("path"), target, False
                )
            )
    return out


def _join(base: str, path: str) -> str | None:
    parts: list[str] = (
        [p for p in PurePosixPath(base).parts if p != "."] if base else []
    )
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _inside(target: str, directory: str) -> bool:
    return target == directory or target.startswith(directory + "/")


def shared_dir(repo: SourceRepo, sid: str) -> str:
    """The `shared-material/` directory next to a skill, repo-relative."""
    return posixpath.join(
        posixpath.dirname(repo.rel(repo.skills[sid].path)), SHARED_DIR
    )


def analyze(
    repo: SourceRepo,
    layout: Layout,
    agensi: bool,
    files: dict[str, list[str]],
    extras: list[str],
    texts: dict[str, str],
) -> Uploads:
    """What an upload of `layout` drops and cannot resolve.

    `agensi` says whether an `agensi` target uses the layout. `files` is the
    lock's skill id -> files, `texts` the delivered SKILL.md text of each skill.
    """
    out = Uploads()
    plugin = layout.shape == PLUGIN
    skills = [sid for sid in files if not repo.skills[sid].is_shared]
    for sid in skills:
        meta = frontmatter(texts.get(sid, ""))
        out.errors += spec_errors(sid, meta)
        dropped = [k for k in meta if k not in AGENSI_KEYS]
        if agensi and dropped:
            out.dropped.setdefault(AGENSI, {})[sid] = dropped
    if agensi:
        out.errors += _agensi_limits(repo, files, len(skills))

    shipped_dirs = {sid: repo.rel(repo.skills[sid].path) for sid in files}
    for sid in skills:
        skill_dir = shipped_dirs[sid]
        plugin_dir = posixpath.dirname(posixpath.dirname(skill_dir))
        shared = shared_dir(repo, sid)
        shared_ships = any(shipped_dirs[s] == shared for s in files)
        for rel in files[sid]:
            if any(w in PurePosixPath(rel).parts[:-1] for w in _EXEMPLAR_WELLS):
                continue
            src = f"{skill_dir}/{rel}"
            text = _read(repo, src)
            if text is None:
                continue
            for ref in path_refs(text, src, skill_dir, plugin_dir):
                if not (repo.root / ref.target).exists():
                    continue  # an illustration, or a dead link the lock reports
                at = f"{sid}/{rel}:{ref.line}"
                if (
                    agensi
                    and not _inside(ref.target, skill_dir)
                    and not (shared_ships and _inside(ref.target, shared))
                ):
                    out.unresolved.append(Unresolved(AGENSI, at, ref.path))
                # the one plugin holds every shipped skill and extra
                if plugin and not (
                    any(_inside(ref.target, d) for d in shipped_dirs.values())
                    or any(_inside(ref.target, e) for e in extras)
                ):
                    out.unresolved.append(Unresolved(PLUGIN, at, ref.path))
    return out


def _agensi_limits(
    repo: SourceRepo, files: dict[str, list[str]], skills: int
) -> list[str]:
    """What an agent upload to Agensi refuses: too many files or bytes, binaries, bundles."""
    out = []
    paths = [
        f"{repo.rel(repo.skills[sid].path)}/{rel}"
        for sid, rels in files.items()
        for rel in rels
    ]
    count = len(paths) + (1 if skills > 1 else 0)  # the generated README
    size = sum((repo.root / p).stat().st_size for p in paths)
    if skills > AGENSI_MAX_SKILLS:
        out.append(
            f"`{AGENSI}`: {skills} skills; a pack holds at most {AGENSI_MAX_SKILLS}"
        )
    if count > AGENSI_MAX_FILES:
        out.append(
            f"`{AGENSI}`: {count} files; an upload from an agent takes at most {AGENSI_MAX_FILES}"
        )
    if size > AGENSI_MAX_BYTES:
        out.append(
            f"`{AGENSI}`: {size} bytes; an upload from an agent takes at most {AGENSI_MAX_BYTES}"
        )
    for p in paths:
        parts = PurePosixPath(p).parts
        if p.lower().endswith(EXECUTABLES):
            out.append(f"`{AGENSI}`: `{p}` is an executable, which Agensi refuses")
        elif any(b in parts for b in BUNDLED):
            out.append(
                f"`{AGENSI}`: `{p}` is a bundled dependency, which Agensi refuses"
            )
        elif _read(repo, p) is None:
            out.append(
                f"`{AGENSI}`: `{p}` is not UTF-8 text; an upload from an agent takes text only"
            )
    return out


def _read(repo: SourceRepo, path: str) -> str | None:
    data = (repo.root / path).read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


# -- the archives ------------------------------------------------------------

Contents = dict[str, tuple[bytes, int]]  # path in the zip -> (data, mode)


def _relink(
    data: bytes, file: str, skill_dir: str, plugin_dir: str, shared: str, copy: str
) -> bytes:
    """Paths into `shared` pointed at `copy`, where the upload puts it, in source terms."""
    if b"\0" in data[:8192]:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for ref in reversed(path_refs(text, file, skill_dir, plugin_dir)):
        if not _inside(ref.target, shared):
            continue
        new = copy + ref.target[len(shared) :]
        base = skill_dir if ref.anchored else posixpath.dirname(file)
        path = posixpath.relpath(new, base)
        if ref.path.endswith("/") and not path.endswith("/"):
            path += "/"
        text = text[: ref.start] + path + text[ref.end :]
    return text.encode("utf-8")


def agensi_archive(
    repo: SourceRepo,
    layout: Layout,
    lock: dict[str, Any],
    delivered: Contents,
    title: str,
) -> Contents:
    """The files of the Agensi package: a skill pack, or a single skill when one ships."""
    entries = {e["id"]: e for e in lock.get("skills", [])}
    skills = [s for s in entries if not repo.skills[s].is_shared]
    shared_ids = [s for s in entries if repo.skills[s].is_shared]
    pack = len(skills) > 1
    out: Contents = {}
    rows = []
    for sid in sorted(skills, key=lambda s: repo.skills[s].name):
        skill = repo.skills[sid]
        home = skill.plugin or repo.home_plugin_name()
        skill_dir = repo.rel(skill.path)
        plugin_dir = posixpath.dirname(posixpath.dirname(skill_dir))
        shared = shared_dir(repo, sid)
        # where `shared/` sits, in source terms: beside `skills/` in a pack, inside a lone skill
        copy = (
            f"{plugin_dir}/{AGENSI_SHARED}" if pack else f"{skill_dir}/{AGENSI_SHARED}"
        )
        base = f"skills/{skill.name}/" if pack else ""
        for rel in entries[sid]["files"]:
            data, mode = delivered[layout.skill_file(home, skill.name, rel)]
            data = _relink(
                data, f"{skill_dir}/{rel}", skill_dir, plugin_dir, shared, copy
            )
            if rel == "SKILL.md":
                text, _ = sanitize(data.decode("utf-8"), AGENSI_KEYS)
                data = text.encode("utf-8")
                description = " ".join(
                    str(frontmatter(text).get("description", "")).split()
                ).replace("|", "\\|")
                rows.append(f"| `{skill.name}` | {description} |")
            out[base + rel] = (data, mode)
    for sid in shared_ids:
        home = repo.skills[sid].plugin or repo.home_plugin_name()
        for rel in entries[sid]["files"]:
            dest = layout.skill_file(home, SHARED_DIR, rel)
            out[f"{AGENSI_SHARED}/{rel}"] = delivered[dest]
    if pack:
        readme = [
            f"# {title}",
            "",
            "| Skill | Description |",
            "| --- | --- |",
            *rows,
            "",
        ]
        out["README.md"] = ("\n".join(readme).encode("utf-8"), 0o644)
    for d, v in delivered.items():
        p = PurePosixPath(d)
        if p.parent == layout.root and p.name.startswith("LICENSE"):
            out.setdefault(p.name, v)
    return out
