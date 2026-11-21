#!/usr/bin/env python3
"""Lint the YAML frontmatter of every skill, agent, and command in a plugin repo.

Claude Code drops a component whose frontmatter is invalid YAML *silently* -- no
error, no warning, it simply never registers. The classic cause is an unquoted
`description:` whose text contains a `: ` (colon + space), e.g. the
`TRIGGER when:` idiom, which YAML rejects outright ("mapping values are not
allowed here"). That is the defect this linter exists to catch.

Everything else it enforces is a house rule. Claude Code is more permissive than
this on every point -- strictly, every frontmatter field is optional, a skill with
no `name` falls back to its directory, and an unrecognized key is simply ignored.
That permissiveness is what makes the failures confusing, so this repo declines to
rely on it. The rules keep a component's identity and permissions legible from the
file alone:

  1. `description` is present, a string, and written as a `>-` folded block scalar.
  2. `name` is present and a string. (A command is keyed by its filename and has
     none -- not a defect there.)
  3. A skill's `name` equals its directory name. Claude Code resolves the two
     differently by location -- in a plugin the frontmatter wins and renames the
     command, elsewhere the directory wins -- so keeping them equal removes the
     question instead of answering it.
  4. No frontmatter key Claude Code does not read, and no `version` anywhere --
     not top-level, not under `metadata:`. Nothing resolves or compares a skill
     version, so the field only invites bumps that mean nothing. What a skill
     needs installed belongs in its `## Setup` / `## Prerequisites` section,
     where a human reads it. `metadata:` holds exactly what the plugin checks
     read: `requires` and `suggests`, the skills this one cannot work without
     and its optional companions, each one string of whitespace-separated
     references (the Agent Skills spec types `metadata` values as strings).
     `check-skill-crossrefs.py` checks what they name, as `REQUIRE`. Any other
     key under `metadata:` is as inert as an invented top-level one.
  5. Every `allowed-tools` / `disallowed-tools` entry names a real tool in
     canonical PascalCase. These fields are live: they grant permission, so a
     name matching nothing is a grant that silently does nothing.
  6. `description` is at most 1024 characters and contains no `<tag>`, a
     skill's `name` at most 64 and lowercase-kebab, and `compatibility` at most
     500 -- the limits the Agent Skills spec sets
     (https://agentskills.io/specification) plus claude.ai's ban on XML tags in
     a description. Claude Code enforces none of them, so an over-long
     description works perfectly on the machine that wrote it; the claude.ai
     marketplace sync rejects it and publishes the plugin *without* that skill,
     and it strips the angle brackets out of a `<placeholder>`. This is the
     one rule whose breakage no local use can reveal, which is why it gets its
     own verdict.

Note what is deliberately *not* checked: the quoting of `name`. It is a kebab-case
identifier, so quoted and unquoted parse to the same string and a repo-wide
requote is churn that buries real findings. See "Quoting: load-bearing for
`description`, cosmetic for `name`" in references/skill-frontmatter.md.

Usage:
    python check-skill-frontmatter.py [REPO_ROOT]   # default: current directory

Verdicts:
    BROKEN  the component does not exist at runtime. Fix before anything else.
    SPEC    runs locally, but an Agent Skills spec limit (rule 6) makes the
            claude.ai marketplace sync drop the skill on publish.
    HOUSE   parses and runs, but breaks one of the remaining rules. The detail
            says which.
    OK      nothing to fix.

Exit status: 0 = every component OK, 1 = at least one BROKEN, SPEC, or HOUSE.
"""

from __future__ import annotations

import glob
import os
import re
import sys
from collections import Counter, defaultdict

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml (or: uv run --with pyyaml ...)")

# Every frontmatter field Claude Code reads. Anything else parses but is inert --
# nothing rejects it and nothing consumes it, which is exactly why an invented key
# reads like a contract it cannot be. Custom data belongs under `metadata:`.
KNOWN_FIELDS = frozenset(
    """
    name description when_to_use argument-hint arguments disable-model-invocation
    user-invocable allowed-tools disallowed-tools model effort context agent
    background hooks paths shell metadata license compatibility
    """.split()
)

# Built-in tool names, canonical PascalCase, as used in permission rules.
KNOWN_TOOLS = frozenset(
    """
    Agent Artifact AskUserQuestion Bash CronCreate CronDelete CronList Edit
    EndConversation EnterPlanMode EnterWorktree ExitPlanMode ExitWorktree Glob
    Grep ListAgents ListMcpResourcesTool LSP Monitor NotebookEdit PowerShell
    PushNotification Read ReadMcpResourceTool RemoteTrigger ReportFindings
    ScheduleWakeup SendFeedback SendMessage SendUserFile ShareOnboardingGuide
    Skill TaskCreate TaskGet TaskList TaskOutput TaskStop TaskUpdate TodoWrite
    ToolSearch WaitForMcpServers WebFetch WebSearch Workflow Write
    """.split()
)
TOOL_FIELDS = ("allowed-tools", "disallowed-tools")

# The keys under `metadata:` that the plugin checks read (rule 4).
METADATA_FIELDS = ("requires", "suggests")

# Agent Skills spec limits -- https://agentskills.io/specification. Claude Code
# ignores all of them; the claude.ai marketplace sync does not, and a skill that
# breaks one is dropped from the published plugin rather than reported to whoever
# wrote it.
MAX_DESCRIPTION = 1024
MAX_NAME = 64
MAX_COMPATIBILITY = 500

# Lowercase alphanumerics and single interior hyphens: the spec's `name` shape.
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Both single-plugin (skills/ at repo root) and multi-plugin (plugins/*/skills/) layouts.
PATTERNS = (
    "skills/*/SKILL.md",
    "plugins/*/skills/*/SKILL.md",
    "agents/*.md",
    "plugins/*/agents/*.md",
    "commands/*.md",
    "commands/*/*.md",
    "plugins/*/commands/*.md",
    "plugins/*/commands/*/*.md",
)


def split_frontmatter(text: str) -> str | None:
    """Return the raw frontmatter block, or None if the file has no frontmatter.

    Splits on the first two `---` fences only, so `---` inside the body (a
    horizontal rule, a nested YAML example) is left alone.
    """
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else None


def scalar_style(fm_text: str, description: str) -> str:
    """Classify the `description:` value as written in the file.

    `description` is the *parsed* value, needed to tell an ordinary quoted string
    (normalize it to `>-`) from one whose escape sequences were interpreted into
    real newlines, which must stay double-quoted -- see the reference's
    ESCAPED case.
    """
    for line in fm_text.split("\n"):
        if line.startswith("description:"):
            value = line[len("description:") :].strip()
            if value.startswith(">"):
                return "FOLDED"
            if value.startswith("|"):
                return "LITERAL"
            if value.startswith(('"', "'")):
                return "ESCAPED" if "\n" in description else "QUOTED"
            return "CONTINUED" if value == "" else "PLAIN"
    return "NO-DESCRIPTION"


def tool_names(value: object) -> list[str]:
    """Split an allowed-tools / disallowed-tools value into bare tool names.

    The field accepts a YAML list or a space/comma-separated string, and an entry
    may carry a specifier -- `Bash(git add *)`, `WebFetch(domain:example.com)`.
    Only the head is a tool name, so the specifier is stripped before checking.
    """
    if isinstance(value, str):
        entries = re.split(r"[,\s]+", value.strip())
    elif isinstance(value, list):
        entries = [str(v) for v in value]
    else:
        return []
    return [e.split("(", 1)[0].strip() for e in entries if e.strip()]


def check(path: str) -> list[tuple[str, str]]:
    """Return every (verdict, detail) issue for one component file.

    Independent rules report independently -- a skill can be HOUSE for its name
    and HOUSE for a tool typo at once -- so one finding never masks another.
    """
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        return [("BROKEN", f"unreadable: {exc}")]

    fm_text = split_frontmatter(text)
    if fm_text is None:
        return [("BROKEN", "no YAML frontmatter block")]

    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        return [("BROKEN", str(exc).split("\n")[0])]

    if not isinstance(fm, dict):
        return [("BROKEN", f"frontmatter is {type(fm).__name__}, not a mapping")]

    is_skill = os.path.basename(path) == "SKILL.md"
    is_command = "commands" in os.path.normpath(path).replace("\\", "/").split("/")
    issues: list[tuple[str, str]] = []

    # Rule 1 -- description. Two different runtime failures land here: the colon
    # trap is a hard YAML error caught above, while a value that parses yet is not
    # a string -- an indented nested block, or a bare number like `2.0` -- reaches
    # this branch instead.
    if "description" not in fm:
        issues.append(("HOUSE", "no `description`"))
    elif not isinstance(fm["description"], str):
        got = type(fm["description"]).__name__
        issues.append(("BROKEN", f"`description` loaded as {got}, not str"))
    else:
        style = scalar_style(fm_text, fm["description"])
        if style not in ("FOLDED", "ESCAPED"):
            issues.append(("HOUSE", f"`description` is {style}, not a `>-` folded block"))
        # Rule 6, the half that actually bites. A folded `>` keeps its trailing
        # newline and a `>-` does not, so a description sitting exactly on the
        # limit can pass or fail on the chomp indicator alone -- one more reason
        # to write these well short of 1024 rather than up against it.
        n = len(fm["description"])
        if n > MAX_DESCRIPTION:
            issues.append((
                "SPEC",
                f"`description` is {n} chars, over the spec's {MAX_DESCRIPTION} "
                f"-- the claude.ai marketplace sync drops this skill",
            ))
        # Rule 6, claude.ai's half: it reports a `<...>` in a description as an
        # XML tag and stores the text with the brackets stripped, so a
        # `<space>-logs` placeholder reaches claude.ai as `space-logs`. A lone
        # `>` (`batchSize > 1`) is not reported, so it is not flagged here.
        tags = sorted(set(re.findall(r"<[^<>\n]*>", fm["description"])))
        if tags:
            issues.append((
                "SPEC",
                f"`description` contains {', '.join(tags)} -- claude.ai rejects XML "
                f"tags and strips the brackets; write placeholders as {{name}}",
            ))

    # Rules 2 and 3 -- name, for everything keyed by one.
    if not is_command:
        if "name" not in fm:
            issues.append(("HOUSE", "no `name`"))
        elif not isinstance(fm["name"], str):
            issues.append(("BROKEN", f"`name` loaded as {type(fm['name']).__name__}, not str"))
        elif is_skill:
            expected = os.path.basename(os.path.dirname(path))
            if fm["name"] != expected:
                issues.append(("HOUSE", f'name "{fm["name"]}" != directory "{expected}"'))
            # Rule 6 for `name`. Only skills are Agent Skills spec objects, so
            # agents keep whatever casing their author chose.
            if len(fm["name"]) > MAX_NAME:
                issues.append(("SPEC", f"`name` is {len(fm['name'])} chars, over the spec's {MAX_NAME}"))
            elif not SKILL_NAME_RE.match(fm["name"]):
                issues.append((
                    "SPEC",
                    f'`name` "{fm["name"]}" is not lowercase-kebab -- a-z, 0-9 and '
                    f"single interior hyphens only",
                ))

    # Rule 4 -- no invented fields, and no version anywhere. The version ban is
    # separate because `metadata:` is a legitimate field: nesting a version under
    # it would satisfy the unknown-key check while keeping the thing the rule
    # exists to stop -- a number that looks maintained and is not.
    # `version` is excluded here so it reports once, under the dedicated rule
    # below -- otherwise it draws two findings whose advice disagrees ("move it
    # under metadata" vs "remove it").
    unknown = sorted(k for k in fm if k not in KNOWN_FIELDS and k != "version")
    if unknown:
        names = ", ".join(f"`{k}`" for k in unknown)
        issues.append(("HOUSE", f"{names} not read by Claude Code -- remove it"))

    if "metadata" in fm and not isinstance(fm["metadata"], dict):
        issues.append(("HOUSE", f"`metadata` is {type(fm['metadata']).__name__}, not a mapping"))
    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    for where, keys in (("", fm), ("metadata.", metadata)):
        if "version" in keys:
            issues.append(("HOUSE", f"`{where}version` -- nothing reads a skill version; remove it"))
    stray = sorted(str(k) for k in metadata if k not in METADATA_FIELDS and k != "version")
    if stray:
        names = ", ".join(f"`metadata.{k}`" for k in stray)
        allowed = " and ".join(f"`{k}`" for k in METADATA_FIELDS)
        issues.append(("HOUSE", f"{names} not read by any tooling -- `metadata` holds {allowed} only"))
    for key in METADATA_FIELDS:
        if key in metadata and not isinstance(metadata[key], str):
            got = type(metadata[key]).__name__
            issues.append((
                "HOUSE",
                f"`metadata.{key}` is {got}, not a string -- write the references as one "
                f"whitespace-separated `>-` block",
            ))

    # Rule 5 -- real tool names, canonical casing.
    for field in TOOL_FIELDS:
        for tool in tool_names(fm.get(field)):
            if tool.startswith("mcp__") or tool in KNOWN_TOOLS:
                continue
            canonical = next((k for k in KNOWN_TOOLS if k.lower() == tool.lower()), None)
            hint = f" -- did you mean `{canonical}`?" if canonical else ""
            issues.append(("HOUSE", f"`{field}` names unknown tool `{tool}`{hint}"))

    # Rule 6, last part. Rare enough that most repos never carry the field at all.
    compat = fm.get("compatibility")
    if isinstance(compat, str) and len(compat) > MAX_COMPATIBILITY:
        issues.append(("SPEC", f"`compatibility` is {len(compat)} chars, over the spec's {MAX_COMPATIBILITY}"))

    return issues or [("OK", "")]


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted({os.path.normpath(f) for p in PATTERNS for f in glob.glob(os.path.join(root, p))})
    if not files:
        print(f"no skills, agents, or commands found under {os.path.abspath(root)}")
        return 0

    results = [(verdict, detail, f) for f in files for verdict, detail in check(f)]
    counts = Counter(verdict for verdict, _, _ in results)

    print(f"{len(files)} component(s) checked under {os.path.abspath(root)}\n")
    for verdict in ("BROKEN", "SPEC", "HOUSE", "OK"):
        if counts[verdict]:
            print(f"  {verdict:7} {counts[verdict]}")

    for verdict in ("BROKEN", "SPEC", "HOUSE"):
        hits = [(f, detail) for v, detail, f in results if v == verdict]
        if not hits:
            continue
        note = {
            "BROKEN": "dead at runtime -- Claude Code drops these silently",
            "SPEC": "fine locally -- the claude.ai marketplace sync drops these on publish",
            "HOUSE": "runs, but breaks a house rule -- see the module docstring",
        }[verdict]
        print(f"\n=== {verdict} ({len(hits)}) -- {note} ===")
        per_dir = defaultdict(list)
        for f, detail in hits:
            per_dir[os.path.dirname(os.path.dirname(f))].append((f, detail))
        for _, entries in sorted(per_dir.items()):
            for f, detail in entries:
                rel = os.path.relpath(f, root)
                print(f"   {rel}" + (f"   <-- {detail}" if detail else ""))

    return 1 if counts["BROKEN"] or counts["SPEC"] or counts["HOUSE"] else 0


if __name__ == "__main__":
    sys.exit(main())
