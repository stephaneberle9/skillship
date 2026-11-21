#!/usr/bin/env python3
"""Catch the drift that happens *between* skills in a plugin repo.

`check-skill-frontmatter.py` checks one component at a time and cannot see this
class of defect: a skill that evolves while a sibling holding the same material
does not. Nothing errors, nothing warns, and both files keep reading as
confident and current -- which is what makes the stale one dangerous. The live
example this script was written from: `docker-compose-powershell-launcher` spent
months telling launchers to delete `COMPOSE_CONVERT_WINDOWS_PATHS` as
"Compose-v1-only dead code" while `docker-wsl2-setup` deliberately set it, and
the only reason it surfaced was a human reading both in one sitting.

Semantic contradictions are not mechanically detectable -- that one needed
someone to read compose-go. What *is* detectable is the structural residue drift
leaves behind, which is what this script checks:

  1. LINK    A relative link or path reference inside a skill that resolves to
             nothing. This is what a rename leaves behind: `examples/compose.ps1`
             was once `start-service.ps1`, and the knowledge base still cited the
             old name months later.
  2. CLONE   Byte-identical files in two or more skills. Not yet a contradiction
             -- it is the state every contradiction starts in. Copies carry no
             signal that the others exist, so a fix lands in one and the rest go
             stale silently. Hoist into the same well of the shared sibling
             directory -- `shared-material/references/` for prose,
             `shared-material/scripts/` for an executable (knowledge base,
             *Sharing material across skills*). Files declared as copies (see
             COPY) are exempt.
  3. SHARED  Shared material outside the one `shared-material/` directory, or a
             `shared-material/` that breaks its own layout. A per-kind
             `shared-<well>/` sibling is the legacy layout: each such directory
             carried its own README repeating the same rules, and the next kind
             of shared file meant a third copy. Any other directory among the
             skills without a `SKILL.md` is either a skill that lost its entry
             point -- the loader silently drops it -- or shared material under a
             name nobody looks for. Inside `shared-material/`: a `SKILL.md`
             (it would register as a skill), a missing root README, and files
             outside the six wells.
  4. STALE   A file under some skill's `templates/` whose path matches a file in
             the repo (`templates/markdownlint.jsonc` vs `.markdownlint.jsonc`,
             `templates/github/workflows/run-static-checks.yml` vs
             `.github/workflows/run-static-checks.yml` -- the first path segment may
             gain a leading dot) and no longer matches its content. The root copy is the one people
             edit; the template is the one that ships. Note the drift here is
             usually in the *comments*: the rules stayed equal while the reasons
             behind them improved in only one copy, so comparing parsed values
             misses exactly the half that matters. One exception: a
             `.pre-commit-config.yaml` passes when it starts with the whole
             template and only adds hooks below it, since that is how the
             `git-hooks-setup` baseline is meant to be used. Example: the
             template holds gitleaks, codespell and prettier; a repo file of
             those exact lines plus a plugin-checks hook appended passes, while
             one that bumps prettier's `rev` in place, or drops codespell,
             is STALE -- change the template, then the repo file.
  5. ECHO    Two identical fenced code blocks back to back in one document --
             the signature of a bad paste, and invisible in review because the
             second copy reads as intentional emphasis.
  6. INDEX   A skill directory missing from README.md, or a README skill row
             naming a directory that does not exist. A skill nobody can find is
             a skill nobody maintains. In a multi-plugin repo the plugin's own
             README.md is checked when it exists, else the root README.md.
  7. ESCAPE  A relative link inside a skill of a multi-plugin repo that leaves
             its plugin (`plugins/<name>/`). Each plugin is cached on its own
             at install time and Claude Code rejects paths outside the plugin
             root, so the link resolves in the repo and is dead for every user.
             Name the other plugin's skill instead, and declare that plugin in
             `dependencies` if the skill cannot work without it.
  9. COPY    A declared copy that no longer matches its original, or a
             declaration that is incomplete. Copying is the exception to
             hoisting: it is right only where the consumer cannot resolve a
             shared file or a dependency -- typically an external packager that
             bundles one skill folder on its own. Such copies are declared in
             `scripts/plugin-checks/local/copies.json`, each group with its
             `original`, its `copies` and the `reason`:

                 {"copies": [{"original": "plugins/a/skills/x/references/f.md",
                              "copies": ["plugins/b/skills/y/references/f.md"],
                              "reason": "Packager Z bundles one skill folder"}]}

             A declared group is exempt from CLONE, and every copy must stay
             byte-identical to its original.
  8. DEPEND  Plugin dependencies that do not match what the skills rely on, in
             a multi-plugin repo. Two shapes: (a) a skill naming a skill that
             lives in another plugin of the same marketplace -- as `/skill`,
             `plugin:skill`, or "the `skill` skill" -- while that plugin is not
             among its own plugin's `dependencies`, followed transitively,
             because Claude Code installs the dependencies of a dependency too.
             The reference works in this repo and for whoever happened to
             install both plugins, and names nothing for everyone else. A
             paragraph saying "when installed" or "if installed" (German:
             "wenn/falls/sofern installiert") is exempt:
             that is the sanctioned phrasing for a reference against the
             dependency direction. A bare backticked name is not checked -- the
             naming conventions cite other plugins' skills as illustrations,
             not as something to load. (b) The umbrella -- the skill-less
             plugin named like the marketplace -- whose `dependencies` are not
             exactly every other plugin of the marketplace, so its one install
             command no longer means the full set.
 10. REQUIRE A skill's declared skill dependencies that do not match what its
             files rely on. A skill declares the skills it cannot work without
             in its frontmatter, as one whitespace-separated string (the Agent
             Skills spec types `metadata` values as strings):

                 metadata:
                   requires: >-
                     sibling-skill
                     other-plugin:their-skill
                     shared-material
                   suggests: >-
                     other-plugin:nice-to-have

             A bare name is a skill of the same plugin, `plugin:skill` one of
             another plugin, and `shared-material` (or `plugin:shared-material`)
             the shared directory. `requires` is what tooling follows when it
             packages a subset of the skills; `suggests` names companions that
             are never pulled in on their own. Plugin `dependencies` cannot
             carry this: they say which plugin needs which plugin, not which
             skill needs which skill. Three shapes are reported: (a) a hard
             reference -- a `read: skills/<x>/...` line, a relative link into
             another skill directory or the shared directory, or a
             `<skill-dir>/../<x>/` path -- whose target is missing from
             `requires`; (b) a `requires` or `suggests` entry that names no
             skill of this repo, names the skill itself, or sits in both lists;
             (c) a `requires` entry in another plugin that the skill's own
             plugin does not depend on, directly or transitively; (d) a
             `suggests` entry naming a skill that itself requires this one --
             a provider declaring its consumer, which would have every
             recipient of the provider offered the skills built on it. Shape (a) is
             mechanical, so `--write-requires` fixes it. A skill handed a step
             in prose ("delegate to X", "run X, then come back") is a
             dependency this check cannot see: declare it by hand.

Deliberately *not* checked: links inside `templates/` and `examples/`. Those
files describe some *other* repository -- a template README linking `LICENSE` or
`plugins/<name>/README.md` means the generated repo's file, not this one, and
checking them produces noise that buries the real findings. Only `SKILL.md`,
`references/`, and the repo's own top-level docs are treated as claims about
this repository.

Usage:
    python check-skill-crossrefs.py [REPO_ROOT]   # default: current directory
    python check-skill-crossrefs.py --write-requires [REPO_ROOT]

`--write-requires` adds every hard reference that `metadata.requires` lacks to
the skill's frontmatter, keeps the entries already there, and reports nothing
else. Run it, read the diff, then run the check.

Verdicts:
    LINK / CLONE / SHARED / STALE / ECHO / INDEX / ESCAPE / DEPEND / COPY / REQUIRE  as above; each line names the fix.

Exit status: 0 = nothing found, 1 = at least one finding. `--write-requires` exits 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

# Where skills live in either layout. The shared directory is a sibling of the
# skills and is scanned with them -- they hold material for two or more
# skills, so they are exactly where a broken reference hurts most.
SKILL_ROOTS = ("skills", "plugins/*/skills")

# Wells whose contents describe the *target* repo rather than this one.
EXEMPLAR_WELLS = ("templates", "examples")

# The supporting-file wells a skill's own relative links point into.
WELLS = frozenset(("references", "templates", "scripts", "assets", "examples", "tests"))

# Where a repo declares the copies it keeps on purpose (COPY).
COPIES_FILE = "scripts/plugin-checks/local/copies.json"

# The one sibling of the skills that holds material two or more of them share.
SHARED_DIR = "shared-material"
LEGACY_SHARED = re.compile(r"shared-(.+)")

# A link target with any of these is a placeholder, not a path: `<skill-dir>`,
# `[plugin-name]`, `{crate}`, an ellipsis.
PLACEHOLDER = re.compile(r"[<>\[\]{}]|\.\.\.|…")

MD_LINK = re.compile(r"\[[^\]]{0,120}\]\(([^)\s]+)\)")
FENCE = re.compile(r"^([ \t]*)(```+|~~~+)([^\n]*)\n(.*?)^\1\2[ \t]*$", re.S | re.M)


def skill_dirs(root: str) -> list[str]:
    """Every skill directory (and the shared-material sibling) under either layout."""
    out = []
    for pattern in SKILL_ROOTS:
        base = os.path.join(root, *pattern.split("/"))
        for parent in _expand_star(base):
            if not os.path.isdir(parent):
                continue
            out += [os.path.join(parent, d) for d in sorted(os.listdir(parent))
                    if os.path.isdir(os.path.join(parent, d))]
    return out


def _expand_star(path: str) -> list[str]:
    if "*" not in path:
        return [path]
    head, _, tail = path.partition("*")
    head = head.rstrip(os.sep)
    if not os.path.isdir(head):
        return []
    return [os.path.join(head, d) + tail for d in sorted(os.listdir(head))]


def is_exemplar(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(w in parts for w in EXEMPLAR_WELLS)


def checked_docs(root: str, dirs: list[str]) -> list[str]:
    """Markdown that makes claims about *this* repo: SKILL.md, references/, top docs."""
    docs = [os.path.join(root, n) for n in ("README.md", "CONTRIBUTING.md", "CONNECTORS.md", "TUTORIAL.md")]
    for d in dirs:
        for cur, _, files in os.walk(d):
            if is_exemplar(cur):
                continue
            docs += [os.path.join(cur, f) for f in files if f.endswith(".md")]
    return [p for p in docs if os.path.isfile(p)]


def check_links(root: str, docs: list[str], basenames: set[str], skills: dict[str, str]) -> list[tuple[str, str, str]]:
    """Two link shapes are claims about this repo; everything else is illustration.

    A `../sibling/...` link is a cross-skill reference and is checked strictly --
    that shape exists for no other reason. A `references/x.md` link is checked
    only when nothing anywhere in the repo carries that basename: a skill quoting
    *another* skill's file to demonstrate the convention ("Copy
    [templates/cf-deploy.sh](templates/cf-deploy.sh) into the project") is not a
    dead link, and flagging it teaches people to ignore the linter. A path into
    some other repository entirely (`eks/apps/.../deployment.yaml` in a review
    example) is skipped for the same reason.
    """
    findings = []
    for doc in docs:
        text = read(doc)
        base = os.path.dirname(doc)
        for m in MD_LINK.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if PLACEHOLDER.search(target):
                continue
            path = target.split("#")[0]
            if not path:
                continue
            first = path.replace("\\", "/").split("/")[0]
            if first == "..":
                pass  # cross-skill reference: always checked
            elif first in WELLS and os.path.basename(path) not in basenames:
                pass  # own supporting file, and nothing in the repo answers to that name
            else:
                continue
            resolved = os.path.normpath(os.path.join(base, path))
            home = plugin_root(root, doc)
            if home and first == ".." and not is_inside(resolved, home):
                line = text[: m.start()].count("\n") + 1
                findings.append(("ESCAPE", f"{rel(doc, root)}:{line}",
                                 f"`{target}` leaves plugin `{os.path.basename(home)}` -- "
                                 "dead once installed; name the skill instead"))
                continue
            if not os.path.exists(resolved):
                line = text[: m.start()].count("\n") + 1
                findings.append(("LINK", f"{rel(doc, root)}:{line}",
                                 f"`{target}` resolves to nothing -- renamed or moved?"))
        findings += check_named_refs(root, doc, text, skills)
    return findings


def check_named_refs(root: str, doc: str, text: str, skills: dict[str, str]) -> list[tuple[str, str, str]]:
    """Backticked `<skill-name>/<well>/<file>` references, checked against that skill.

    Not every path in prose is worth checking, but this shape is: it names a
    sibling skill that exists in this repo, so the rest of it either exists too
    or is a rename nobody followed through. The knowledge base's own folder-role
    table cited `docker-compose-powershell-launcher/examples/start-service.ps1`
    long after that file became `compose.ps1` -- a plain markdown-link check
    never sees it, because the citation is inline code, not a link.
    """
    findings = []
    for m in re.finditer(r"`([A-Za-z0-9._-]+)/(" + "|".join(sorted(WELLS)) + r")/([A-Za-z0-9._/-]+)`", text):
        skill, well, tail = m.groups()
        if skill not in skills:
            continue  # names a skill from some other repo -- an example, not a claim
        if not os.path.exists(os.path.join(skills[skill], well, tail)):
            line = text[: m.start()].count("\n") + 1
            findings.append(("LINK", f"{rel(doc, root)}:{line}",
                             f"`{skill}/{well}/{tail}` names a real skill but no such file -- renamed?"))
    return findings


def check_clones(root: str, dirs: list[str], declared: list[set[str]]) -> list[tuple[str, str, str]]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for d in dirs:
        for cur, _, files in os.walk(d):
            for f in files:
                p = os.path.join(cur, f)
                blob = open(p, "rb").read()
                if len(blob.strip()) < 200:  # a stub or a near-empty file proves nothing
                    continue
                by_hash[hashlib.sha256(blob).hexdigest()].append(p)
    findings = []
    for paths in by_hash.values():
        owners = {owning_skill(p, dirs) for p in paths}
        if len(owners) < 2:
            continue
        if any({rel(p, root) for p in paths} <= group for group in declared):
            continue
        first, *rest = sorted(rel(p, root) for p in paths)
        # Name the right home: the well the file already sits in. A folder called
        # `references` is the wrong place for a script, in the shared directory
        # exactly as it is inside a skill.
        p0 = min(paths, key=lambda p: rel(p, root))
        well = os.path.relpath(p0, owning_skill(p0, dirs)).replace("\\", "/").split("/")[0]
        if well not in WELLS:
            well = "references" if first.endswith(".md") else "scripts"
        home = f"{SHARED_DIR}/{well}/"
        findings.append(("CLONE", first,
                         "byte-identical to " + ", ".join(rest)
                         + f" -- hoist into {home} and give it one owner"))
    return findings


def check_shared(root: str, dirs: list[str]) -> list[tuple[str, str, str]]:
    """Shared material lives in `shared-material/<well>/` and nowhere else."""
    findings = []
    for d in dirs:
        name, where = os.path.basename(d), rel(d, root)
        if name == SHARED_DIR:
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                findings.append(("SHARED", f"{where}/SKILL.md",
                                 "the shared directory is not a skill -- delete the SKILL.md, or "
                                 "make this a library skill under its own name"))
            if not os.path.isfile(os.path.join(d, "README.md")):
                findings.append(("SHARED", where,
                                 "no root README.md -- without it the missing SKILL.md reads as "
                                 "forgotten; seed it from claude-plugin-scaffold's "
                                 "templates/shared-material-readme.md"))
            for entry in sorted(os.listdir(d)):
                path = os.path.join(d, entry)
                if os.path.isdir(path) and entry not in WELLS:
                    findings.append(("SHARED", f"{where}/{entry}",
                                     "not one of the six wells -- move its files into the well "
                                     "they would occupy inside a skill"))
                elif os.path.isfile(path) and entry not in ("README.md", "SKILL.md"):
                    findings.append(("SHARED", f"{where}/{entry}",
                                     "loose at the shared directory's root -- move it into the "
                                     "well it would occupy inside a skill"))
            continue
        if os.path.isfile(os.path.join(d, "SKILL.md")):
            continue
        m = LEGACY_SHARED.fullmatch(name)
        if m:
            target = f"{SHARED_DIR}/{m.group(1)}/" if m.group(1) in WELLS else f"{SHARED_DIR}/<well>/"
            findings.append(("SHARED", where,
                             f"legacy per-kind shared directory -- move its files to {target}, "
                             f"merge its README into {SHARED_DIR}/README.md, repoint consumers"))
        else:
            findings.append(("SHARED", where,
                             f"no SKILL.md and not {SHARED_DIR}/ -- a skill missing its entry "
                             f"point, or shared material that belongs in {SHARED_DIR}/<well>/"))
    return findings


def exists_exact(root: str, relpath: str) -> bool:
    """Whether `relpath` exists under `root` with exactly this spelling.

    Compared against directory listings rather than os.path.isfile: on Windows and
    macOS the filesystem is case-insensitive, so `templates/contributing.md` (a
    placeholder skeleton for other repos) would "match" the root `CONTRIBUTING.md`.
    """
    cur = root
    for part in relpath.split("/"):
        if not os.path.isdir(cur) or part not in os.listdir(cur):
            return False
        cur = os.path.join(cur, part)
    return os.path.isfile(cur)


def check_stale_templates(root: str, dirs: list[str]) -> list[tuple[str, str, str]]:
    """A shipped template must match the repo's own copy of the same file."""
    findings = []
    for d in dirs:
        tdir = os.path.join(d, "templates")
        if not os.path.isdir(tdir):
            continue
        for cur, _, files in os.walk(tdir):
            for name in sorted(files):
                tpl = os.path.join(cur, name)
                trel = rel(tpl, tdir)
                for candidate_rel in (trel, "." + trel):
                    if not exists_exact(root, candidate_rel):
                        continue
                    candidate = os.path.join(root, *candidate_rel.split("/"))
                    want, have = open(tpl, "rb").read(), open(candidate, "rb").read()
                    extends = candidate_rel == ".pre-commit-config.yaml" and have.startswith(want)
                    if have != want and not extends:
                        findings.append(("STALE", rel(tpl, root),
                                         f"differs from {candidate_rel} -- the two must match; bring "
                                         "whichever is stale up to date (for scripts/plugin-checks/ the "
                                         "template is the source)"))
    return findings


def load_copies(root: str) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """The declared copy groups, and findings for a declaration that cannot be read."""
    path = os.path.join(root, *COPIES_FILE.split("/"))
    if not os.path.isfile(path):
        return [], []
    try:
        groups = json.loads(read(path)).get("copies", [])
    except (ValueError, AttributeError) as exc:
        return [], [("COPY", COPIES_FILE, f"not valid JSON with a `copies` list ({exc})")]
    if not isinstance(groups, list):
        return [], [("COPY", COPIES_FILE, "`copies` is not a list")]
    return [g for g in groups if isinstance(g, dict)], []


def check_copies(root: str, groups: list[dict]) -> list[tuple[str, str, str]]:
    findings = []
    for n, group in enumerate(groups, start=1):
        where = f"{COPIES_FILE} group {n}"
        original, copies = group.get("original"), group.get("copies")
        if not isinstance(original, str) or not isinstance(copies, list) or not copies:
            findings.append(("COPY", where, "needs an `original` path and a non-empty `copies` list"))
            continue
        if not str(group.get("reason", "")).strip():
            findings.append(("COPY", where, "has no `reason` -- say why this cannot be hoisted or "
                                            "shared through a dependency"))
        source = os.path.join(root, *original.split("/"))
        if not os.path.isfile(source):
            findings.append(("COPY", original, f"declared as an original in {COPIES_FILE} but missing"))
            continue
        blob = open(source, "rb").read()
        for copy in copies:
            target = os.path.join(root, *str(copy).split("/"))
            if not os.path.isfile(target):
                findings.append(("COPY", str(copy), f"declared as a copy of {original} but missing"))
            elif open(target, "rb").read() != blob:
                findings.append(("COPY", str(copy), f"differs from its original {original} -- "
                                                    "change the original and copy it over again"))
    return findings


def check_echoes(root: str, docs: list[str]) -> list[tuple[str, str, str]]:
    findings = []
    for doc in docs:
        text = read(doc)
        blocks = [(m.start(), m.group(3).strip(), m.group(4)) for m in FENCE.finditer(text)]
        for (_, lang_a, body_a), (start_b, lang_b, body_b) in zip(blocks, blocks[1:]):
            if body_a.strip() and body_a == body_b and lang_a == lang_b:
                line = text[:start_b].count("\n") + 1
                findings.append(("ECHO", f"{rel(doc, root)}:{line}",
                                 "repeats the previous code block verbatim -- duplicate paste?"))
    return findings


def check_index(root: str, dirs: list[str]) -> list[tuple[str, str, str]]:
    findings = []
    for d in dirs:
        name = os.path.basename(d)
        if not os.path.isfile(os.path.join(d, "SKILL.md")):
            continue  # the shared directory is deliberately not listed
        home = plugin_root(root, d)
        readme = os.path.join(home, "README.md") if home else ""
        if not os.path.isfile(readme):
            readme = os.path.join(root, "README.md")
        if not os.path.isfile(readme):
            continue
        if not re.search(rf"`/?{re.escape(name)}`", read(readme)):
            findings.append(("INDEX", rel(d, root),
                             "not listed in README.md -- a skill nobody can find is one "
                             "nobody maintains"))
    return findings


def load_marketplace(root: str) -> tuple[str, dict[str, tuple[str, list[str]]]]:
    """Marketplace name and {plugin name: (plugin dir, same-marketplace dependencies)}.

    Only plugins with a relative-path `source` are covered -- a plugin fetched from
    another repository has no skills here to check. A dependency on another
    marketplace is dropped: it cannot provide a skill of this repo.
    """
    try:
        market = json.loads(read(os.path.join(root, ".claude-plugin", "marketplace.json")))
    except (OSError, ValueError):
        return "", {}
    name = market.get("name", "")
    base = (market.get("metadata") or {}).get("pluginRoot", "")
    plugins: dict[str, tuple[str, list[str]]] = {}
    for entry in market.get("plugins", []):
        source = entry.get("source")
        if not isinstance(source, str):
            continue
        if not source.startswith("./") and base:
            source = base.rstrip("/") + "/" + source
        pdir = os.path.normpath(os.path.join(root, source))
        try:
            manifest = json.loads(read(os.path.join(pdir, ".claude-plugin", "plugin.json")))
        except (OSError, ValueError):
            manifest = {}
        deps = []
        for dep in manifest.get("dependencies", []):
            if isinstance(dep, dict):
                dep_name, dep_market = dep.get("name", ""), dep.get("marketplace") or name
            else:
                dep_name, _, dep_market = str(dep).partition("@")
                dep_market = dep_market or name
            if dep_market == name:
                deps.append(dep_name)
        plugins[entry.get("name") or manifest.get("name") or os.path.basename(pdir)] = (pdir, deps)
    return name, plugins


# How a skill relies on another skill by name: `/skill`, `plugin:skill`, or
# "the `skill` skill" / "skill `skill`". A bare backticked name is an illustration.
SKILL_MENTION = re.compile(r"`(/)?(?:([a-z0-9-]+):)?([a-z0-9-]+)`")
CONDITIONAL = re.compile(r"\b(?:(?:when|if)\s+installed|(?:wenn|falls|sofern)\s+installiert)\b", re.I)
PARAGRAPH = re.compile(r"(?:[^\n]*\S[^\n]*(?:\n|$))+")


def check_dependencies(root: str, docs: list[str]) -> list[tuple[str, str, str]]:
    market, plugins = load_marketplace(root)
    if len(plugins) < 2:
        return []
    name_of_dir = {os.path.abspath(pdir): pname for pname, (pdir, _) in plugins.items()}
    skills_of: dict[str, set[str]] = {}
    providers: dict[str, set[str]] = defaultdict(set)
    for pname, (pdir, _) in plugins.items():
        sdir = os.path.join(pdir, "skills")
        names = {d for d in (os.listdir(sdir) if os.path.isdir(sdir) else [])
                 if os.path.isfile(os.path.join(sdir, d, "SKILL.md"))}
        skills_of[pname] = names
        for n in names:
            providers[n].add(pname)

    findings = []
    for doc in docs:
        home = plugin_root(root, doc)
        pname = name_of_dir.get(os.path.abspath(home)) if home else None
        if not pname:
            continue
        ok = reachable_plugins(plugins, pname)
        text = read(doc)
        for para in PARAGRAPH.finditer(text):
            block = para.group(0)
            if CONDITIONAL.search(block):
                continue
            for m in SKILL_MENTION.finditer(block):
                slash, ns, skill = m.groups()
                homes = providers.get(skill, set())
                if ns:
                    homes = homes & {ns}
                if not homes or homes & ok:
                    continue
                before = block[max(0, m.start() - 8):m.start()]
                after = block[m.end():m.end() + 8]
                if not (slash or ns or re.match(r"\s+skills?\b", after)
                        or re.search(r"\bskills?\s+$", before)):
                    continue
                line = text[: para.start() + m.start()].count("\n") + 1
                provider = ", ".join(f"`{h}`" for h in sorted(homes))
                findings.append(("DEPEND", f"{rel(doc, root)}:{line}",
                                 f"names `{skill}` from {provider}, which `{pname}` does not "
                                 "depend on -- add it to plugin.json `dependencies`, or phrase "
                                 "it \"..., when installed\""))

    umbrella = plugins.get(market)
    if umbrella and not skills_of.get(market):
        where = rel(os.path.join(umbrella[0], ".claude-plugin", "plugin.json"), root)
        want, have = set(plugins) - {market}, set(umbrella[1])
        for missing in sorted(want - have):
            findings.append(("DEPEND", where,
                             f"umbrella `{market}` does not depend on `{missing}` -- its install "
                             "command no longer installs the full set"))
        for extra in sorted(have - want):
            findings.append(("DEPEND", where,
                             f"umbrella `{market}` depends on `{extra}`, which the marketplace "
                             "does not list"))
    return findings


def reachable_plugins(plugins: dict[str, tuple[str, list[str]]], pname: str) -> set[str]:
    """`pname` and every plugin its `dependencies` bring along, transitively."""
    seen, stack = {pname}, list(plugins[pname][1])
    while stack:
        dep = stack.pop()
        if dep not in seen and dep in plugins:
            seen.add(dep)
            stack += plugins[dep][1]
    return seen


# --- REQUIRE: declared skill dependencies -----------------------------------

# The hard-reference shapes beside a relative link: a line telling the model to
# load a sibling's file, and a path built from the skill's own directory.
READ_LINE = re.compile(r"^[ \t]*read:[ \t]*`?skills/([A-Za-z0-9._-]+)/", re.M)
SKILL_DIR_PATH = re.compile(r"<skill-dir>/\.\./([A-Za-z0-9._-]+)(?![A-Za-z0-9._-])")
REQUIRE_KEYS = ("requires", "suggests")
REQUIRE_REF = re.compile(r"(?:([a-z0-9]+(?:-[a-z0-9]+)*):)?([a-z0-9]+(?:-[a-z0-9]+)*)")
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*(?:\r?\n|\Z)", re.S)


def read_raw(path: str) -> str:
    """Like `read`, with line endings left as they are -- for a file about to be rewritten."""
    return open(path, encoding="utf-8", newline="").read()


def parse_requires(fm: str) -> dict:
    """Where `metadata.requires` / `suggests` sit in a frontmatter block, and what they hold.

    Read line by line instead of through a YAML library: this script has no
    dependency, and `--write-requires` has to change two keys while leaving every
    other byte of the frontmatter alone. `check-skill-frontmatter.py` parses the
    same block as YAML and rejects a value that is not a string.

    Returns `lines` (the block split into lines), `metadata` (index of the
    `metadata:` line, or None), `flow` (True for an inline `metadata: {...}`,
    which is not read), `indent` (of the keys under `metadata:`) and, per key
    found, `(first line, line after the last, [entries])`.
    """
    lines = fm.splitlines()
    out: dict = {"lines": lines, "metadata": None, "flow": False, "indent": "  "}
    for i, line in enumerate(lines):
        m = re.match(r"metadata:[ \t]*(.*)$", line)
        if m:
            out["metadata"] = i
            out["flow"] = bool(m.group(1)) and not m.group(1).startswith("#")
            break
    if out["metadata"] is None or out["flow"]:
        return out
    end = out["metadata"] + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
        end += 1
    block = range(out["metadata"] + 1, end)
    first = next((lines[i] for i in block if lines[i].strip()), "")
    indent = first[: len(first) - len(first.lstrip())]
    if not indent:
        return out
    out["indent"] = indent
    i = out["metadata"] + 1
    while i < end:
        m = re.match(re.escape(indent) + r"(" + "|".join(REQUIRE_KEYS) + r"):[ \t]*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        key, value = m.groups()
        j = i + 1
        while j < end and (not lines[j].strip() or (lines[j].startswith(indent) and lines[j][len(indent)] in " \t")):
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1  # blank lines after the value belong to whatever follows
        value = "" if value[:1] in (">", "|") else re.sub(r"(^|\s)#.*$", "", value).strip("\"' \t")
        text = " ".join([value] + [ln.strip().strip("\"'") for ln in lines[i + 1 : j]])
        out[key] = (i, j, text.split())
        i = j
    return out


def render_requires(indent: str, entries: list[str]) -> list[str]:
    return [f"{indent}requires: >-"] + [f"{indent}{indent}{e}" for e in entries]


def skill_graph(root: str, dirs: list[str]) -> dict[str, list[tuple[str, str, int, str]]]:
    """Per skill directory, its hard references: (target directory, document, line, shape)."""
    absdirs = [os.path.abspath(d) for d in dirs]
    graph: dict[str, list[tuple[str, str, int, str]]] = {}
    for d in absdirs:
        if not os.path.isfile(os.path.join(d, "SKILL.md")):
            continue
        siblings = {os.path.basename(o): o for o in absdirs if os.path.dirname(o) == os.path.dirname(d)}
        refs = []
        for doc in checked_docs(root, [d])[::-1]:
            if not is_inside(doc, d):
                continue  # the repo's top-level docs belong to no skill
            text = read(doc)
            for pattern, shape in ((READ_LINE, "a `read:` line"), (SKILL_DIR_PATH, "a `<skill-dir>/../` path")):
                for m in pattern.finditer(text):
                    refs.append((siblings.get(m.group(1), ""), doc, text[: m.start()].count("\n") + 1, shape))
            for m in MD_LINK.finditer(text):
                target = m.group(1).split("#")[0]
                if not target or target.startswith(("http://", "https://", "mailto:")) or PLACEHOLDER.search(target):
                    continue
                resolved = os.path.abspath(os.path.join(os.path.dirname(doc), target))
                if not os.path.exists(resolved):
                    continue  # LINK reports it
                owner = next((o for o in absdirs if is_inside(resolved, o)), "")
                refs.append((owner, doc, text[: m.start()].count("\n") + 1, "a relative link"))
        graph[d] = [r for r in refs if r[0] and r[0] != d and is_requirable(r[0])]
    return graph


def is_requirable(d: str) -> bool:
    return os.path.basename(d) == SHARED_DIR or os.path.isfile(os.path.join(d, "SKILL.md"))


def ref_label(source: str, target: str) -> str:
    """How `source` names `target` in `metadata.requires`: bare within a plugin, else `plugin:skill`."""
    name = os.path.basename(target)
    if os.path.dirname(source) == os.path.dirname(target):
        return name
    return f"{os.path.basename(os.path.dirname(os.path.dirname(target)))}:{name}"


def resolve_ref(root: str, source: str, ref: str) -> str:
    """The directory a `requires` entry of `source` names, or '' when it names nothing."""
    m = REQUIRE_REF.fullmatch(ref)
    if not m:
        return ""
    plugin, name = m.groups()
    parent = os.path.join(os.path.abspath(root), "plugins", plugin, "skills") if plugin else os.path.dirname(source)
    target = os.path.join(parent, name)
    return target if os.path.isdir(target) and is_requirable(target) else ""


def declared_requires(skill: str) -> tuple[dict, str]:
    """The parsed `metadata` of a skill's frontmatter, and a problem with reading it ('' if none)."""
    m = FRONTMATTER.match(read_raw(os.path.join(skill, "SKILL.md")))
    if not m:
        return {}, ""  # check-skill-frontmatter.py reports a skill without frontmatter
    parsed = parse_requires(m.group(1))
    if parsed["flow"]:
        return {}, "`metadata` is written inline -- write it as an indented block so its keys can be read"
    return parsed, ""


def check_requires(root: str, dirs: list[str]) -> list[tuple[str, str, str]]:
    _, plugins = load_marketplace(root)
    name_of_dir = {os.path.abspath(pdir): pname for pname, (pdir, _) in plugins.items()}
    findings = []
    graph = skill_graph(root, dirs)
    requires_of = {
        skill: {resolve_ref(root, skill, ref) for ref in declared_requires(skill)[0].get("requires", (0, 0, []))[2]}
        for skill in graph
    }

    def requires_transitively(skill: str, target: str) -> bool:
        seen, stack = set(), [skill]
        while stack:
            for nxt in requires_of.get(stack.pop(), ()):
                if nxt == target:
                    return True
                if nxt and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    for skill, refs in graph.items():
        where = rel(os.path.join(skill, "SKILL.md"), root)
        parsed, problem = declared_requires(skill)
        if problem:
            findings.append(("REQUIRE", where, problem))
            continue
        requires = parsed.get("requires", (0, 0, []))[2]
        suggests = parsed.get("suggests", (0, 0, []))[2]
        home = plugin_root(root, skill)
        own = name_of_dir.get(os.path.abspath(home)) if home else None
        ok = reachable_plugins(plugins, own) if own else set()
        covered = set()
        for key, entries in (("requires", requires), ("suggests", suggests)):
            for ref in entries:
                target = resolve_ref(root, skill, ref)
                if not target:
                    findings.append(("REQUIRE", where,
                                     f"`metadata.{key}` names `{ref}`, which is no skill or shared "
                                     "directory of this repo -- renamed, removed, or missing its "
                                     "`plugin:` prefix?"))
                elif target == skill:
                    findings.append(("REQUIRE", where, f"`metadata.{key}` names the skill itself -- remove `{ref}`"))
                elif key == "suggests" and requires_transitively(target, skill):
                    findings.append(("REQUIRE", where,
                                     f"`metadata.suggests` names `{ref}`, which itself requires this skill "
                                     "-- a provider does not declare its consumers; remove the entry"))
                elif key == "requires":
                    covered.add(target)
                    there = name_of_dir.get(os.path.abspath(plugin_root(root, target) or root))
                    if own and there and there not in ok:
                        findings.append(("REQUIRE", where,
                                         f"`metadata.requires` names `{ref}`, but plugin `{own}` does "
                                         f"not depend on `{there}` -- add it to plugin.json `dependencies`"))
        for ref in sorted(set(requires) & set(suggests)):
            findings.append(("REQUIRE", where, f"`{ref}` is in both `requires` and `suggests` -- keep it in one"))
        missing: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
        for target, doc, line, shape in refs:
            if target not in covered:
                missing[target].append((rel(doc, root), line, shape))
        for target, hits in sorted(missing.items()):
            doc, line, shape = min(hits)
            more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
            findings.append(("REQUIRE", f"{doc}:{line}",
                             f"{shape} into `{ref_label(skill, target)}`{more}, which `metadata.requires` "
                             f"of `{os.path.basename(skill)}` does not list -- add it, or run this "
                             "script with --write-requires"))
    return findings


def write_requires(root: str, dirs: list[str]) -> int:
    """Add every undeclared hard reference to `metadata.requires`. Returns the number of skills changed."""
    changed = 0
    for skill, refs in sorted(skill_graph(root, dirs).items()):
        path = os.path.join(skill, "SKILL.md")
        text = read_raw(path)
        m = FRONTMATTER.match(text)
        where = rel(path, root)
        if not refs:
            continue
        if not m:
            print(f"   {where}   <-- skipped: no frontmatter block")
            continue
        parsed = parse_requires(m.group(1))
        if parsed["flow"]:
            print(f"   {where}   <-- skipped: `metadata` is written inline; write it as an indented block")
            continue
        start, end, have = parsed.get("requires", (None, None, []))
        covered = {resolve_ref(root, skill, ref) for ref in have}
        add = sorted({ref_label(skill, t) for t, _, _, _ in refs if t not in covered})
        if not add:
            continue
        lines, indent = parsed["lines"], parsed["indent"]
        block = render_requires(indent, have + add)
        if start is not None:
            lines[start:end] = block
        elif parsed["metadata"] is not None:
            lines[parsed["metadata"] + 1 : parsed["metadata"] + 1] = block
        else:
            lines += ["metadata:"] + block
        nl = "\r\n" if "\r\n" in m.group(1) else "\n"
        fm = nl.join(lines) + nl
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text[: m.start(1)] + fm + text[m.end(1) :])
        changed += 1
        print(f"   {where}   <-- added {', '.join(add)}")
    return changed


def plugin_root(root: str, path: str) -> str:
    """`<root>/plugins/<name>` when `path` lies inside one, else ''."""
    parts = rel(path, root).split("/")
    if len(parts) > 2 and parts[0] == "plugins":
        return os.path.join(root, "plugins", parts[1])
    return ""


def is_inside(path: str, parent: str) -> bool:
    path, parent = os.path.abspath(path), os.path.abspath(parent)
    return os.path.commonpath([path, parent]) == parent


def owning_skill(path: str, dirs: list[str]) -> str:
    for d in dirs:
        if os.path.commonpath([os.path.abspath(path), os.path.abspath(d)]) == os.path.abspath(d):
            return d
    return os.path.dirname(path)


def repo_basenames(root: str) -> set[str]:
    """Every file name in the repo, used to tell an illustration from a dead link."""
    names = set()
    for cur, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d != ".git"]
        names.update(files)
    return names


def read(path: str) -> str:
    return open(path, encoding="utf-8", errors="replace").read()


def rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


KINDS = ("LINK", "ESCAPE", "DEPEND", "REQUIRE", "COPY", "CLONE", "SHARED", "STALE", "ECHO", "INDEX")


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--write-requires"]
    root = args[0] if args else "."
    dirs = skill_dirs(root)
    if not dirs:
        print(f"no skills found under {os.path.abspath(root)}")
        return 0

    if "--write-requires" in sys.argv[1:]:
        print(f"writing `metadata.requires` under {os.path.abspath(root)}\n")
        changed = write_requires(root, dirs)
        print(f"\n  {changed} skill(s) changed" + ("" if changed else " -- every hard reference is declared"))
        print("  Not covered: a skill handed a step in prose. Declare those by hand.")
        return 0

    docs = checked_docs(root, dirs)
    copies, copy_errors = load_copies(root)
    declared = [{g["original"], *map(str, g["copies"])} for g in copies
                if isinstance(g.get("original"), str) and isinstance(g.get("copies"), list)]
    findings = (
        copy_errors
        + check_copies(root, copies)
        + check_links(root, docs, repo_basenames(root), {os.path.basename(d): d for d in dirs})
        + check_clones(root, dirs, declared)
        + check_shared(root, dirs)
        + check_stale_templates(root, dirs)
        + check_echoes(root, docs)
        + check_index(root, dirs)
        + check_dependencies(root, docs)
        + check_requires(root, dirs)
    )

    print(f"{len(dirs)} skill dir(s), {len(docs)} document(s) checked under {os.path.abspath(root)}\n")
    counts = Counter(kind for kind, _, _ in findings)
    if not findings:
        print("  OK      no cross-skill drift found")
        return 0

    for kind in KINDS:
        if counts[kind]:
            print(f"  {kind:7} {counts[kind]}")
    for kind in KINDS:
        hits = [(where, detail) for k, where, detail in findings if k == kind]
        if not hits:
            continue
        print(f"\n=== {kind} ({len(hits)}) ===")
        for where, detail in sorted(hits):
            print(f"   {where}   <-- {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
