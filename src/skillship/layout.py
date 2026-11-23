"""Where each delivered file goes, per layout.

| Layout                             | Skills                                     | Plugin files (manifest, README, MCP extras) |
| ---------------------------------- | ------------------------------------------ | ------------------------------------------- |
| `skills`                           | `<root>/skills/<skill>/`                   | `<root>/` (README and MCP extras only)      |
| `marketplace`                      | `<root>/plugins/<plugin>/skills/<skill>/`  | `<root>/plugins/<plugin>/`                  |
| `marketplace` with `merged-plugin` | `<root>/plugins/<name>/skills/<skill>/`    | `<root>/plugins/<name>/`                    |

A marketplace adds `<root>/.claude-plugin/marketplace.json`, listing the
delivered plugins, so a recipient can add the tree as it is. It keeps the
source's plugins, one or several, unless `merged-plugin` names the one plugin
they are merged into.

The `skills` shape and a merged marketplace **flatten**: the source plugins do
not survive, so their files share one directory, and text naming them is
rewritten (see `rewrite`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from skillship.errors import SkillshipError

SHAPES = ("skills", "marketplace")
MARKETPLACE = ".claude-plugin/marketplace.json"
MANIFEST = ".claude-plugin/plugin.json"
README = "README.md"


def check_name(name: str) -> str:
    """`name` when it is one path component, as a plugin directory name must be."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise SkillshipError(f"the plugin name `{name}` is not a directory name")
    return name


def check_dest(path: str) -> str:
    """`path` when it stays inside the delivery tree and outside `.git`."""
    p = PurePosixPath(path)
    if not p.parts or p.is_absolute() or ".." in p.parts or p.parts[0] == ".git":
        raise SkillshipError(f"`{path}` leaves the delivery tree")
    return path


@dataclass(frozen=True)
class Layout:
    shape: str
    root: PurePosixPath
    merged: str | None = None  # the plugin a marketplace merges its source plugins into

    @classmethod
    def from_lock(cls, data: dict[str, Any]) -> Layout:
        layout = data.get("layout", {})
        shape = layout.get("shape")
        if shape not in SHAPES:
            raise SkillshipError(
                f"the lock's layout shape `{shape}` is unknown -- run `skillship lock`"
            )
        root = PurePosixPath(layout.get("root", "."))
        if root.is_absolute() or ".." in root.parts:
            raise SkillshipError(
                f"the lock's layout root `{root}` leaves the delivery tree"
            )
        merged = layout.get("merged-plugin")
        if merged:
            if shape != "marketplace":
                raise SkillshipError(
                    f"the lock's layout has a `merged-plugin` in shape `{shape}`"
                )
            check_name(merged)
        return cls(shape, root, merged or None)

    @property
    def is_marketplace(self) -> bool:
        return self.shape == "marketplace"

    @property
    def flattens(self) -> bool:
        """Whether the source plugins do not survive: in `skills`, and in a merged marketplace."""
        return not self.is_marketplace or self.merged is not None

    @property
    def label(self) -> str:
        """What a message calls this layout, by the profile key that chose it."""
        return "`merged-plugin`" if self.merged else f"shape `{self.shape}`"

    @property
    def unflattened_hint(self) -> str:
        """The profile change that keeps the source plugins apart."""
        if self.merged:
            return "drop `merged-plugin`"
        return "use shape `marketplace`"

    def plugin_name(self, home: str | None) -> str | None:
        """The delivered plugin that receives the files of source plugin `home`."""
        if self.is_marketplace and not self.merged:
            return home
        return self.merged  # None in shape `skills`

    def plugin_dir(self, home: str | None) -> PurePosixPath:
        if not self.is_marketplace:
            return self.root
        name = self.plugin_name(home)
        if not name:
            raise SkillshipError(
                "shape `marketplace` needs every shipped skill in a plugin, but the "
                "source repo's root skills have no `.claude-plugin/plugin.json` naming one"
            )
        return self.root / "plugins" / check_name(name)

    def skill_file(self, home: str | None, skill: str, rel: str) -> str:
        return (self.plugin_dir(home) / "skills" / skill / rel).as_posix()

    def plugin_file(self, home: str | None, rel: str) -> str:
        return (self.plugin_dir(home) / rel).as_posix()

    def root_file(self, rel: str) -> str:
        return (self.root / rel).as_posix()

    def generated(
        self, plugins: list[str], readme: bool
    ) -> dict[str, tuple[str, str | None]]:
        """The files skillship writes itself: delivered path -> (kind, delivered plugin)."""
        out: dict[str, tuple[str, str | None]] = {}
        if self.is_marketplace:
            out[self.root_file(MARKETPLACE)] = ("marketplace", None)
            for p in plugins:
                base = self.root / "plugins" / check_name(p)
                out[(base / MANIFEST).as_posix()] = ("manifest", p)
                out[(base / README).as_posix()] = ("readme", p)
        elif readme:
            out[self.root_file(README)] = ("readme", None)
        return dict(sorted(out.items()))
