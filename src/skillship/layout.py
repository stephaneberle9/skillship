"""Where each delivered file goes, per layout shape.

| Shape     | Skills                                     | Plugin files (manifest, README, MCP extras) |
| --------- | ------------------------------------------ | ------------------------------------------- |
| `skills`  | `<root>/skills/<skill>/`                   | `<root>/` (README and MCP extras only)      |
| `plugins` | `<root>/plugins/<plugin>/skills/<skill>/`  | `<root>/plugins/<plugin>/`                  |
| `plugin`  | `<root>/plugins/<name>/skills/<skill>/`    | `<root>/plugins/<name>/`                    |

The plugin shapes add `<root>/.claude-plugin/marketplace.json`, listing the
delivered plugins, so the tree is a marketplace a recipient can add as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from skillship.errors import SkillshipError

PLUGIN_SHAPES = ("plugins", "plugin")
MARKETPLACE = ".claude-plugin/marketplace.json"
MANIFEST = ".claude-plugin/plugin.json"
README = "README.md"


@dataclass(frozen=True)
class Layout:
    shape: str
    root: PurePosixPath
    name: str | None = None  # the merged plugin, shape `plugin`

    @classmethod
    def from_lock(cls, data: dict[str, Any]) -> Layout:
        layout = data.get("layout", {})
        shape = layout.get("shape")
        if shape not in ("skills", *PLUGIN_SHAPES):
            raise SkillshipError(f"the lock's layout shape `{shape}` is unknown")
        root = PurePosixPath(layout.get("root", "."))
        if root.is_absolute() or ".." in root.parts:
            raise SkillshipError(
                f"the lock's layout root `{root}` leaves the delivery tree"
            )
        name = layout.get("name")
        if shape == "plugin" and not name:
            raise SkillshipError("the lock's layout has shape `plugin` but no `name`")
        return cls(shape, root, name)

    @property
    def is_plugin_shape(self) -> bool:
        return self.shape in PLUGIN_SHAPES

    def plugin_name(self, home: str | None) -> str | None:
        """The delivered plugin that receives the files of source plugin `home`."""
        if self.shape == "plugins":
            return home
        return self.name  # None in shape `skills`

    def plugin_dir(self, home: str | None) -> PurePosixPath:
        if self.shape == "skills":
            return self.root
        name = self.plugin_name(home)
        if not name:
            raise SkillshipError(
                "shape `plugins` needs every shipped skill in a plugin, but the source "
                "repo's root skills have no `.claude-plugin/plugin.json` naming one"
            )
        return self.root / "plugins" / name

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
        if self.is_plugin_shape:
            out[self.root_file(MARKETPLACE)] = ("marketplace", None)
            for p in plugins:
                base = self.root / "plugins" / p
                out[(base / MANIFEST).as_posix()] = ("manifest", p)
                out[(base / README).as_posix()] = ("readme", p)
        elif readme:
            out[self.root_file(README)] = ("readme", None)
        return dict(sorted(out.items()))
