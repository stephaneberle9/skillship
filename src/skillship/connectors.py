"""Merge the `CONNECTORS.md` files of several source plugins into one.

A flattening layout, the `skills` or `plugin` shape or a flattened marketplace,
puts the files of every source plugin into one directory, so their
`CONNECTORS.md` files land on one path. Like the `.mcp.json` files beside them
they are merged, into a document that reads as one:

- the first file's title and introduction open the document; an introduction
  of a later file follows when it says something else;
- `##` sections with the same heading become one section; a section only a
  later file has goes before the next section of that file the document
  already has, so an explanation stays ahead of the table and the steps;
- inside a section, a block (paragraph, list, table, alert, fenced code) that
  an earlier file already has is dropped;
- tables with the same header row become one table: equal rows are listed
  once, and rows that differ only in their last cell become one row with both
  cells, joined by "; ".

A `###` heading that two files use for different text appears twice; reword
one in the source. A link from one of the merged files to another is pointed
at the merged file itself.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

FILE = "CONNECTORS.md"

_LINK = re.compile(r"(\[[^\]]{0,120}\]\()([^)\s]+)(\))")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def retarget(text: str, path: str, merged: list[str]) -> str:
    """`text` of source file `path`, with links to the other merged files pointing at the merged file."""
    base = PurePosixPath(path).parent
    others = {p for p in merged if p != path}

    def fix(m: re.Match[str]) -> str:
        target, _, anchor = m.group(2).partition("#")
        if not target or ":" in target or target.startswith("/"):
            return m.group(0)
        parts: list[str] = []
        for part in [*base.parts, *target.split("/")]:
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return m.group(0)
                parts.pop()
            else:
                parts.append(part)
        if "/".join(parts) not in others:
            return m.group(0)
        return m.group(1) + FILE + (f"#{anchor}" if anchor else "") + m.group(3)

    return _LINK.sub(fix, text)


def merge(texts: list[str]) -> str:
    """The merged document of `texts`, in the order given."""
    texts = [t.replace("\r\n", "\n") for t in texts]
    if len(set(texts)) == 1:
        return texts[0]
    title: str | None = None
    intro: list[str] = []
    order: list[str] = []
    sections: dict[str, list[str]] = {}
    for text in texts:
        head, parts = _sections(text)
        blocks = _blocks(head)
        if blocks and blocks[0].startswith("# "):
            title = title or blocks[0]
            blocks = blocks[1:]
        _add(intro, blocks)
        for i, (heading, body) in enumerate(parts):
            if heading not in sections:
                # before the next section of this file that the document already has
                later = [h for h, _ in parts[i + 1 :] if h in sections]
                at = order.index(later[0]) if later else len(order)
                order.insert(at, heading)
                sections[heading] = []
            _add(sections[heading], _blocks(body))
    out = ([title] if title else []) + intro
    for heading in order:
        out += [heading, *sections[heading]]
    return "\n\n".join(out) + "\n"


def _sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The text before the first `## ` heading, and (heading, body) of each `##` section."""
    head: list[str] = []
    parts: list[tuple[str, list[str]]] = []
    fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            fence = not fence
        if not fence and line.startswith("## "):
            parts.append((line.rstrip(), []))
        elif parts:
            parts[-1][1].append(line)
        else:
            head.append(line)
    return "\n".join(head), [(h, "\n".join(b)) for h, b in parts]


def _blocks(text: str) -> list[str]:
    """`text` split at blank lines outside fenced code."""
    out: list[str] = []
    cur: list[str] = []
    fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            fence = not fence
        if not fence and not line.strip():
            if cur:
                out.append("\n".join(cur))
                cur = []
        else:
            cur.append(line.rstrip() if not fence else line)
    if cur:
        out.append("\n".join(cur))
    return out


def _add(have: list[str], blocks: list[str]) -> None:
    """Append `blocks` to `have`: no block twice, and a table into the table with its header."""
    for block in blocks:
        if block in have:
            continue
        rows = _table(block)
        if rows:
            for i, other in enumerate(have):
                theirs = _table(other)
                if theirs and theirs[0] == rows[0]:
                    have[i] = _render(theirs[0], _rows(theirs[1:] + rows[1:]))
                    break
            else:
                have.append(block)
        else:
            have.append(block)


def _table(block: str) -> list[list[str]] | None:
    """The header and body rows of a block that is a table, as cells; else None."""
    lines = block.split("\n")
    if len(lines) < 2 or "|" not in lines[0] or not _SEPARATOR.match(lines[1]):
        return None
    if any("|" not in line for line in lines[2:]):
        return None
    return [_cells(lines[0]), *(_cells(line) for line in lines[2:])]


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner.removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def _rows(rows: list[list[str]]) -> list[list[str]]:
    """Rows without repeats; rows equal but for their last cell become one."""
    out: list[list[str]] = []
    for row in rows:
        for kept in out:
            if kept == row:
                break
            if len(kept) == len(row) > 1 and kept[:-1] == row[:-1]:
                kept[-1] = f"{kept[-1]}; {row[-1]}"
                break
        else:
            out.append(list(row))
    return out


def _render(header: list[str], rows: list[list[str]]) -> str:
    lines = [header, ["---"] * len(header), *rows]
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)
