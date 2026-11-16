"""The source repo: where its skills are, what each declares, which files it has.

Skills live in a root `skills/` directory, in `plugins/<plugin>/skills/`, or in
both. Next to the skills of one root may sit `shared-material/`, a directory of
files two or more skills use; it is no skill, but a skill can require it like
one. A skill's id is `plugin:name`, or the bare name for a root skill.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from skillship.errors import SkillshipError

SHARED_DIR = "shared-material"
SYMLINK_MODE = "120000"
MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"

# A plugin's non-skill files that `plugin-extras.mcp` delivers, relative to the plugin directory.
MCP_EXTRAS = (".mcp.json", "CONNECTORS.md", "connectors/**")

# Never delivered, whatever the profile says.
GLOBAL_IGNORES = ("**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**")

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_REF = re.compile(r"(?:([a-z0-9]+(?:-[a-z0-9]+)*):)?([a-z0-9]+(?:-[a-z0-9]+)*)")


@dataclass
class Skill:
    id: str
    plugin: str | None
    name: str
    path: Path  # absolute directory
    requires: list[str] = field(default_factory=list)  # resolved ids
    suggests: list[str] = field(default_factory=list)  # resolved ids

    @property
    def is_shared(self) -> bool:
        return self.name == SHARED_DIR


class SourceRepo:
    """A source repo's skills, read once."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.skills: dict[str, Skill] = {}
        self._tracked = _git_files(self.root)
        self._discover()

    # -- discovery ---------------------------------------------------------

    def _discover(self) -> None:
        parents: list[tuple[str | None, Path]] = [(None, self.root / "skills")]
        plugins = self.root / "plugins"
        if plugins.is_dir():
            parents += [
                (p.name, p / "skills") for p in sorted(plugins.iterdir()) if p.is_dir()
            ]
        raw: dict[str, tuple[str, str]] = {}
        for plugin, parent in parents:
            if not parent.is_dir():
                continue
            for d in sorted(parent.iterdir()):
                is_skill = (d / "SKILL.md").is_file()
                if not d.is_dir() or not (is_skill or d.name == SHARED_DIR):
                    continue
                sid = f"{plugin}:{d.name}" if plugin else d.name
                self.skills[sid] = Skill(sid, plugin, d.name, d)
                raw[sid] = _declared(d / "SKILL.md") if is_skill else ("", "")
        for sid, (requires, suggests) in raw.items():
            skill = self.skills[sid]
            skill.requires = [
                self._resolve(skill, r, "requires") for r in requires.split()
            ]
            skill.suggests = [
                self._resolve(skill, r, "suggests") for r in suggests.split()
            ]

    def _resolve(self, skill: Skill, ref: str, key: str) -> str:
        m = _REF.fullmatch(ref)
        plugin, name = (m.group(1) or skill.plugin, m.group(2)) if m else (None, ref)
        sid = f"{plugin}:{name}" if plugin else name
        if not m or sid not in self.skills:
            raise SkillshipError(
                f"{self.rel(skill.path / 'SKILL.md')}: `metadata.{key}` names `{ref}`, "
                "which is no skill of this repo"
            )
        return sid

    # -- queries -----------------------------------------------------------

    def match(self, pattern: str) -> list[str]:
        """Skill ids a profile pattern names: `plugin:glob`, or a glob on the name in any plugin."""
        plugin, _, name = pattern.rpartition(":")
        name_re = glob_to_regex(name)
        return sorted(
            sid
            for sid, s in self.skills.items()
            if name_re.fullmatch(s.name) and (not plugin or plugin == s.plugin)
        )

    def files(self, skill: Skill, excludes: list[str]) -> list[str]:
        """The skill's files, relative to its directory, sorted; git-tracked ones only in a git repo."""
        prefix = self.rel(skill.path) + "/"
        if self._tracked is not None:
            found = [p[len(prefix) :] for p in self._tracked if p.startswith(prefix)]
        else:
            found = [
                p.relative_to(skill.path).as_posix()
                for p in skill.path.rglob("*")
                if p.is_file() or p.is_symlink()
            ]
        drop = [glob_to_regex(g) for g in (*GLOBAL_IGNORES, *excludes)]
        kept = sorted(f for f in found if not any(r.fullmatch(f) for r in drop))
        for f in kept:
            if self.is_symlink(prefix + f):
                # a symlink would deliver whatever it points at, anywhere on the machine
                raise SkillshipError(
                    f"{prefix}{f} is a symlink; skillship does not deliver symlinks -- "
                    "replace it with the file, or exclude it under `files.exclude`"
                )
        return kept

    def is_symlink(self, relpath: str) -> bool:
        if self._tracked is not None and self._tracked.get(relpath) == SYMLINK_MODE:
            return True
        return (self.root / relpath).is_symlink()

    def mode(self, relpath: str) -> int:
        """Unix permission bits for a repo file: 0o755 when git records it executable."""
        if self._tracked is not None:
            return 0o755 if self._tracked.get(relpath) == "100755" else 0o644
        return 0o644

    # -- plugins -----------------------------------------------------------

    def plugin_dir(self, plugin: str | None) -> Path:
        """The directory of a plugin; the repo root for the root skills."""
        return self.root / "plugins" / plugin if plugin else self.root

    def plugin_names(self) -> list[str]:
        """The plugins under `plugins/`, sorted."""
        base = self.root / "plugins"
        return (
            sorted(p.name for p in base.iterdir() if p.is_dir())
            if base.is_dir()
            else []
        )

    def home_plugin_name(self) -> str | None:
        """The name of the plugin the root skills belong to: the root `plugin.json`'s, if any."""
        name = self.manifest(None).get("name")
        return name if isinstance(name, str) and name else None

    def manifest(self, plugin: str | None) -> dict:
        """A plugin's `plugin.json`, or {} when it has none."""
        return self._json(self.plugin_dir(plugin) / MANIFEST)

    def marketplace(self) -> dict:
        """The source repo's `marketplace.json`, or {} when it has none."""
        return self._json(self.root / MARKETPLACE)

    def extras(self, plugin: str | None) -> list[str]:
        """The MCP extras of a plugin, as repo-relative paths, sorted; git-tracked ones only."""
        base = self.plugin_dir(plugin)
        prefix = "" if base == self.root else self.rel(base) + "/"
        wanted = [glob_to_regex(prefix + g) for g in MCP_EXTRAS]
        drop = [glob_to_regex(g) for g in GLOBAL_IGNORES]
        if self._tracked is not None:
            found = list(self._tracked)
        else:
            found = [
                self.rel(p)
                for g in (".mcp.json", "CONNECTORS.md", "connectors")
                for p in (
                    [base / g] if not (base / g).is_dir() else (base / g).rglob("*")
                )
                if p.is_file()
            ]
        kept = sorted(
            f
            for f in found
            if any(r.fullmatch(f) for r in wanted)
            and not any(r.fullmatch(f) for r in drop)
        )
        for f in kept:
            if self.is_symlink(f):
                raise SkillshipError(
                    f"{f} is a symlink; skillship does not deliver symlinks -- replace it with the file"
                )
        return kept

    def _json(self, path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SkillshipError(f"{self.rel(path)} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SkillshipError(f"{self.rel(path)} is not a JSON object")
        return data

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def head(self) -> tuple[str, bool]:
        """The source commit and whether the working tree differs from it ('' when unknown)."""
        sha = _git(self.root, "rev-parse", "HEAD")
        if sha is None:
            return "", False
        dirty = _git(self.root, "status", "--porcelain", "--untracked-files=no")
        return sha.strip(), bool(dirty and dirty.strip())

    def name(self) -> str:
        """The source repo's name: the last path segment of `origin`, else its directory name."""
        url = _git(self.root, "remote", "get-url", "origin")
        if url and url.strip():
            return PurePosixPath(url.strip().replace(":", "/")).name.removesuffix(
                ".git"
            )
        return self.root.name


def _declared(skill_md: Path) -> tuple[str, str]:
    """`metadata.requires` and `metadata.suggests` of a SKILL.md, as raw strings."""
    m = _FRONTMATTER.match(skill_md.read_text(encoding="utf-8"))
    if not m:
        return "", ""
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillshipError(
            f"{skill_md}: frontmatter is not valid YAML: {exc}"
        ) from exc
    meta = fm.get("metadata") if isinstance(fm, dict) else None
    if not isinstance(meta, dict):
        return "", ""
    out = []
    for key in ("requires", "suggests"):
        value = meta.get(key, "")
        if not isinstance(value, str):
            raise SkillshipError(f"{skill_md}: `metadata.{key}` must be a string")
        out.append(value)
    return out[0], out[1]


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _git_files(root: Path) -> dict[str, str] | None:
    """Tracked files, repo-relative, with their git mode; None outside a git work tree."""
    top = (_git(root, "rev-parse", "--show-toplevel") or "").strip()
    if not top or Path(top).resolve() != root:
        return None
    out = _git(root, "ls-files", "-s", "-z")
    if out is None:
        return None
    files = {}
    for entry in out.split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            mode = meta.split()[0]
            # a file deleted in the working tree is not deliverable; a symlink is
            # kept, so that `files` can refuse it
            if mode == SYMLINK_MODE or (root / path).is_file():
                files[path] = mode
    return files


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """A path glob as a regex: `**/` spans directories, `*` and `?` stay within one."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out)
