"""The rewrites of the delivered copy: pruning `suggests`, and flattening.

The source is never changed; these apply to the delivered copy only, and the
lock counts them per file (`skillship lock --explain` prints each one).

**Pruning, every shape.** A delivered SKILL.md keeps only the `metadata.suggests`
entries that ship. An entry naming a skill the recipient does not get points
at nothing in the delivered tree, and the plugin checks fail it as `REQUIRE`.
The lock's `suggested` section still records what was suggested.

**Flattening, shape `skills` and a merged plugin.** In the `skills` shape
no plugin exists, and in the `plugin` shape or a marketplace with `flatten`
only the merged one does, so a plugin-qualified name would name a plugin the recipient never gets.
Only where the named plugin is one of the source repo's and the named skill
ships:

- the call form `/plugin:skill` becomes `/skill` (shape `skills`) or
  `/merged:skill` (merged plugin), and likewise without the slash; inside a
  SKILL.md frontmatter, as in `metadata.requires`, the qualifier is dropped;
- the attribution "the `skill` skill (`plugin` plugin)", or "…, `plugin`
  plugin)", loses the plugin (shape `skills`) or names the merged plugin.

In the `skills` shape, where no plugin exists at all, a pointer to a file of
the skill's own plugin loses the plugin too: "this plugin's `CONNECTORS.md`"
becomes "the `CONNECTORS.md`". With a merged plugin it stays, since the skill
is in a plugin there.

Every other mention of a source plugin that ships skills matches no pattern and
is reported as unflattened, because a guess could change what the text means.
A qualified name or an attribution of a skill that does not ship stays as it is:
it names that skill where it lives upstream, and the dangling report covers it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from skillship.source import _FRONTMATTER

_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_REF = re.compile(rf"(?:({_NAME}):)?({_NAME})")
_SUGGESTS = re.compile(
    r"^(?P<indent>[ \t]+)suggests:[ \t]*(?P<inline>[^\n]*)\n"
    r"(?P<cont>(?:(?P=indent)[ \t]+[^\n]*\n)*)",
    re.MULTILINE,
)
_BLOCK_SCALAR = re.compile(r"[>|][+-]?")
_COND = r"(?:,\s*(?:when|if)\s+installed)?"  # "(`plugin` plugin, when installed)"
_ATTRIBUTIONS = ("pp", "cp", "fp")
# "this plugin's `file`" or "this plugin's [file](…)": a file of the skill's own plugin
_OWN = re.compile(r"\b(?P<t>[Tt])his\s+plugin's(?=\s+[`\[])")


class _Verdict:
    """A match flattening does not rewrite."""


KEEP = _Verdict()  # a name of a skill that does not ship: true upstream, left as it is
REPORT = _Verdict()  # a plugin name no pattern covers: reported as unflattened


@dataclass
class Rewrite:
    line: int  # first line of the change in the source file
    before: str  # the source lines the change touches
    after: str  # the same lines as delivered


@dataclass
class Rewritten:
    text: str
    count: int = 0  # number of rewrites
    rewrites: list[Rewrite] = field(default_factory=list)
    unflattened: list[tuple[str, int]] = field(default_factory=list)  # (plugin, line)


class Rewriter:
    """Rewrites the delivered text of one delivery.

    `skills_of` maps every plugin of the source repo to the names of its skills,
    and `shipped` holds the ids of the skills that ship. `flattening` says
    whether the source plugins do not survive, and `merged` names the plugin
    they are merged into, if any.
    """

    def __init__(
        self,
        skills_of: dict[str, set[str]],
        shipped: set[str],
        flattening: bool,
        merged: str | None = None,
    ) -> None:
        self.skills_of = skills_of
        self.shipped_ids = shipped
        self.shipped = {
            p: {s for s in skills if f"{p}:{s}" in shipped}
            for p, skills in skills_of.items()
        }
        self.flattening = flattening
        self.merged = merged if flattening else None
        plugins = sorted(
            (p for p, s in self.shipped.items() if s), key=lambda p: (-len(p), p)
        )
        self._pattern: re.Pattern[str] | None = None
        if plugins and self.flattening:
            alt = "|".join(re.escape(p) for p in plugins)
            self._pattern = re.compile(
                rf"(?P<paren>\s*\(`?(?P<pp>{alt})`?(?:\s+plugin)?{_COND}\))"
                rf"|(?P<comma>,\s*`?(?P<cp>{alt})`?\s+plugin{_COND}\))"
                rf"|(?P<from>,?\s+from\s+the\s+`?(?P<fp>{alt})`?\s+plugin"
                rf"(?:,?\s+(?:when|if)\s+installed)?)"
                rf"|(?<![\w-])(?P<slash>/?)(?P<qp>{alt}):(?P<qs>{_NAME})(?![\w-])"
                rf"|(?<![\w-])(?P<bare>{alt})(?![\w-])"
            )

    def rewrite(
        self, text: str, skill_md_of: str | None = None, *, plugin: str | None = None
    ) -> Rewritten:
        """Rewrite one delivered file; `skill_md_of` is set for a SKILL.md, with the skill's `plugin`."""
        fm = _FRONTMATTER.match(text) if skill_md_of else None
        fm_end = fm.end() if fm else 0
        edits: list[tuple[int, int, str]] = []
        count = 0
        skip = (0, 0)
        if fm:
            pruned = self._prune(text, fm.start(1), fm.end(1), plugin)
            if pruned:
                start, end, new, n = pruned
                edits.append((start, end, new))
                count += n
                skip = (start, end)
        unflattened: list[tuple[str, int]] = []
        for m in self._pattern.finditer(text) if self._pattern else ():
            if skip[0] <= m.start() < skip[1]:
                continue
            new = self._replacement(text, m, m.start() < fm_end)
            if new is KEEP:
                continue
            if new is REPORT:
                group = next(g for g in (*_ATTRIBUTIONS, "bare") if m.group(g))
                unflattened.append((m.group(group), _line(text, m.start(group))))
                continue
            edits.append((m.start(), m.end(), str(new)))
            count += 1
        if self.flattening and self.merged is None:  # shape `skills`: no plugin at all
            for m in _OWN.finditer(text):
                edits.append((m.start(), m.end(), m.group("t") + "he"))
                count += 1
        edits.sort()
        return Rewritten(
            _apply(text, edits), count, _rewrites(text, edits), unflattened
        )

    # -- pruning -----------------------------------------------------------

    def _prune(
        self, text: str, lo: int, hi: int, plugin: str | None
    ) -> tuple[int, int, str, int] | None:
        """The edit that prunes `metadata.suggests` in the frontmatter text[lo:hi], if any."""
        m = _SUGGESTS.search(text, lo, hi)
        if not m:
            return None
        inline, cont = m.group("inline").strip(), m.group("cont")
        block = bool(_BLOCK_SCALAR.fullmatch(inline)) or not inline
        refs = (cont if block else inline + " " + cont).split()
        kept, changes = [], 0
        for ref in refs:
            r = _REF.fullmatch(ref)
            sid = ref
            if r:
                owner, name = r.group(1) or plugin, r.group(2)
                sid = f"{owner}:{name}" if owner else name
            if sid not in self.shipped_ids:
                changes += 1  # dropped
                continue
            if self.flattening and r and r.group(1):
                ref, changes = r.group(2), changes + 1
            kept.append(ref)
        if not changes:
            return None
        indent = m.group("indent")
        if not kept:
            start, end = m.start(), m.end()
            parent = _metadata_parent(text, lo, start, end, indent)
            if parent is not None:
                start = parent
            return start, end, "", changes
        if block:
            inner = cont[: len(cont) - len(cont.lstrip(" \t"))] or indent + "  "
            body = "".join(f"{inner}{ref}\n" for ref in kept)
            new = f"{indent}suggests: {inline or '>-'}\n{body}"
        else:
            new = f"{indent}suggests: {' '.join(kept)}\n"
        return m.start(), m.end(), new, changes

    # -- flattening --------------------------------------------------------

    def _replacement(
        self, text: str, m: re.Match[str], in_frontmatter: bool
    ) -> str | _Verdict:
        """The new text for a match, KEEP to leave it, or REPORT to report it as unflattened."""
        if m.group("qp"):
            plugin, skill = m.group("qp"), m.group("qs")
            if skill not in self.shipped[plugin]:
                return KEEP  # a skill that does not ship: the dangling report covers it
            if in_frontmatter or self.merged is None:
                return m.group("slash") + skill
            return f"{m.group('slash')}{self.merged}:{skill}"
        if m.group("bare"):
            return REPORT
        group = next(g for g in _ATTRIBUTIONS if m.group(g))
        plugin = m.group(group)
        owner = self._attributed(text, m.start(), plugin)
        if owner is None:
            return REPORT
        if owner not in self.shipped[plugin]:
            return KEEP  # the attribution of a skill that does not ship stays true upstream
        if self.merged is None:
            return ")" if m.group("comma") else ""
        span, start = m.group(0), m.start(group) - m.start()
        return span[:start] + self.merged + span[start + len(plugin) :]

    def _attributed(self, text: str, pos: int, plugin: str) -> str | None:
        """The skill of `plugin` an attribution at `pos` belongs to: the last one named before it in the paragraph."""
        start = text.rfind("\n\n", 0, pos)
        before = text[start + 1 if start >= 0 else 0 : pos]
        names = self.skills_of.get(plugin, set())
        found = None
        for m in re.finditer(rf"(?<![\w-])({_NAME})(?![\w-])", before):
            if m.group(1) in names:
                found = m.group(1)
        return found


def _metadata_parent(
    text: str, lo: int, start: int, end: int, indent: str
) -> int | None:
    """Where the `metadata:` line starts when the span start:end is its only child, else None."""
    newline = text.rfind("\n", lo, start - 1) if start > lo else -1
    head = newline + 1 if newline >= 0 else lo
    if not re.fullmatch(r"metadata:[ \t]*\n", text[head:start]):
        return None
    rest = text[end:]
    if rest.startswith((indent, " ", "\t")):
        return None  # metadata has other children after the span
    return head


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _apply(text: str, edits: list[tuple[int, int, str]]) -> str:
    out, last = [], 0
    for start, end, new in edits:
        out += [text[last:start], new]
        last = end
    out.append(text[last:])
    return "".join(out)


def _rewrites(text: str, edits: list[tuple[int, int, str]]) -> list[Rewrite]:
    """The edits as whole source lines before and after, edits on shared lines merged."""
    groups: list[list[tuple[int, int, str]]] = []
    for edit in edits:
        if groups:
            prev_end = groups[-1][-1][1]
            if "\n" not in text[prev_end : edit[0]]:
                groups[-1].append(edit)
                continue
        groups.append([edit])
    out = []
    for group in groups:
        lo = text.rfind("\n", 0, group[0][0]) + 1
        end = group[-1][1]
        if end > lo and text[end - 1] == "\n":
            end -= 1  # an edit of whole lines: show those lines only
        hi = text.find("\n", end)
        hi = len(text) if hi < 0 else hi
        before = text[lo:hi]
        after = _apply(before, [(s - lo, min(e, hi) - lo, n) for s, e, n in group])
        if group[-1][1] > hi:  # the edit ate the line break after `before`
            after = after.removesuffix("\n")
        out.append(Rewrite(_line(text, lo), before, after))
    return out
