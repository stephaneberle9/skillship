#!/usr/bin/env python3
"""Check the marketplace and plugin manifests of a plugin repo, and the version policy.

`check-skill-frontmatter.py` and `check-skill-crossrefs.py` look at the skills; this
looks at the two JSON files that decide what a marketplace ships at all. A broken
one fails in the least helpful way: the marketplace still lists, and the plugin it
names simply is not there, or ships under a version nobody bumped.

  1. BROKEN  `.claude-plugin/marketplace.json` or a `plugin.json` that is not valid
             JSON, has no `name`, or a marketplace entry whose relative `source`
             points at a directory that does not exist.
  2. HOUSE   A house rule from the knowledge base (`references/manifests.md`):
             - a `version` anywhere in `marketplace.json` -- entry, top level or
               `metadata`. It duplicates `plugin.json` at best, and when the two
               disagree `plugin.json` silently wins;
             - a `plugin.json` whose `name` differs from its marketplace entry;
             - a `version` in `plugin.json` that is not a quoted `x.y.z` string;
             - a directory under `plugins/` that the marketplace does not list,
               which therefore ships to nobody.
  3. BUMP    (only with `--base REF`) A plugin whose files changed since REF while
             its `plugin.json` carries a `version` that did not increase. A pinned
             version holds every Claude Code user on the cached copy until it
             changes, and claude.ai keeps displaying it while shipping newer
             commits -- so a versioned plugin bumps with every release. An
             unversioned plugin (no `version`, the default) is never reported.

Usage:
    python check-plugin-manifests.py [REPO_ROOT] [--base REF]

`--base` is what a pull request or merge request check passes (the target branch,
e.g. `origin/main`); a pre-commit hook leaves it out, because a branch bumps its
version once, not on every commit.

Exit status: 0 = nothing found, 1 = at least one finding.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
KINDS = ("BROKEN", "HOUSE", "BUMP")


def rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def load(path: str, root: str, findings: list) -> dict | None:
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except OSError:
        return None
    except ValueError as exc:
        findings.append(("BROKEN", rel(path, root), f"not valid JSON ({exc})"))
        return None
    if not isinstance(data, dict):
        findings.append(("BROKEN", rel(path, root), "not a JSON object"))
        return None
    return data


def plugin_dirs(root: str, findings: list) -> dict[str, str]:
    """{plugin name: plugin dir} for every plugin the marketplace ships from this repo."""
    market_path = os.path.join(root, ".claude-plugin", "marketplace.json")
    market = load(market_path, root, findings)
    if market is None:
        # A repo without a marketplace manifest can still be a plugin of its own.
        return {"": root} if os.path.isfile(os.path.join(root, ".claude-plugin", "plugin.json")) else {}
    where = ".claude-plugin/marketplace.json"
    if not str(market.get("name", "")).strip():
        findings.append(("BROKEN", where, "no `name`"))
    if "version" in market or "version" in (market.get("metadata") or {}):
        findings.append(("HOUSE", where, "carries a `version` -- the version belongs in plugin.json only"))
    base = (market.get("metadata") or {}).get("pluginRoot", "")
    dirs: dict[str, str] = {}
    for entry in market.get("plugins", []):
        if not isinstance(entry, dict):
            findings.append(("BROKEN", where, "a `plugins` entry that is not an object"))
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            findings.append(("BROKEN", where, "a `plugins` entry without `name`"))
            continue
        if "version" in entry:
            findings.append(("HOUSE", where, f"entry `{name}` carries a `version` -- "
                                             "the version belongs in plugin.json only"))
        source = entry.get("source")
        if not isinstance(source, str):
            continue                      # fetched from another repository: nothing here to check
        if not source.startswith("./") and base:
            source = base.rstrip("/") + "/" + source
        pdir = os.path.normpath(os.path.join(root, source))
        if not os.path.isdir(pdir):
            findings.append(("BROKEN", where, f"entry `{name}`: source {entry.get('source')!r} is no directory"))
            continue
        dirs[name] = pdir
    plugins_root = os.path.join(root, "plugins")
    if os.path.isdir(plugins_root):
        listed = {os.path.normcase(os.path.abspath(d)) for d in dirs.values()}
        for d in sorted(os.listdir(plugins_root)):
            path = os.path.join(plugins_root, d)
            if os.path.isdir(path) and os.path.normcase(os.path.abspath(path)) not in listed:
                findings.append(("HOUSE", rel(path, root), "not listed in marketplace.json -- it ships to nobody"))
    return dirs


def check_plugins(root: str, dirs: dict[str, str], findings: list) -> dict[str, str]:
    """Validate each plugin.json; return {plugin dir (repo-relative): version} for versioned plugins."""
    versions: dict[str, str] = {}
    for name, pdir in dirs.items():
        path = os.path.join(pdir, ".claude-plugin", "plugin.json")
        if not os.path.isfile(path):
            continue                      # the manifest is optional
        manifest = load(path, root, findings)
        if manifest is None:
            continue
        where = rel(path, root)
        actual = str(manifest.get("name", "")).strip()
        if not actual:
            findings.append(("BROKEN", where, "no `name`"))
        elif name and actual != name:
            findings.append(("HOUSE", where, f"`name` {actual!r} differs from its marketplace entry {name!r}"))
        if "version" in manifest:
            version = manifest["version"]
            if not isinstance(version, str) or not SEMVER.match(version):
                findings.append(("HOUSE", where, f"`version` {version!r} is not an `x.y.z` string"))
            else:
                versions[rel(pdir, root)] = version
    return versions


def git(root: str, *args: str) -> str:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          check=True, encoding="utf-8").stdout


def semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def check_bumps(root: str, base: str, versions: dict[str, str], findings: list) -> None:
    try:
        changed = git(root, "diff", "--name-only", f"{base}...HEAD").splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        findings.append(("BROKEN", base, f"cannot diff against {base} ({exc}) -- fetch it, or check out "
                                         "with full history (fetch-depth: 0 / GIT_DEPTH: 0)"))
        return
    for pdir, version in sorted(versions.items()):
        prefix = "" if pdir == "." else pdir + "/"
        if not any(p.startswith(prefix) for p in changed):
            continue
        manifest = (prefix or "") + ".claude-plugin/plugin.json"
        try:
            old = json.loads(git(root, "show", f"{base}:{manifest}")).get("version")
        except (subprocess.CalledProcessError, ValueError):
            continue                      # new plugin, or newly versioned: nothing to compare against
        if not isinstance(old, str) or not SEMVER.match(old):
            continue
        if semver(version) <= semver(old):
            findings.append(("BUMP", manifest, f"plugin changed since {base} but `version` did not increase "
                                               f"({old} -> {version}); raise it, or the update does not reach "
                                               "Claude Code users"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--base", help="ref to compare against for the version policy, e.g. origin/main")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    findings: list[tuple[str, str, str]] = []
    dirs = plugin_dirs(root, findings)
    versions = check_plugins(root, dirs, findings)
    if args.base:
        check_bumps(root, args.base, versions, findings)

    print(f"{len(dirs)} plugin(s) checked under {root}"
          + (f", version policy against {args.base}" if args.base else "") + "\n")
    if not findings:
        print("  OK      manifests and versions conform")
        return 0
    counts = Counter(kind for kind, _, _ in findings)
    for kind in KINDS:
        if counts[kind]:
            print(f"  {kind:7} {counts[kind]}")
    for kind in KINDS:
        hits = sorted((where, detail) for k, where, detail in findings if k == kind)
        if hits:
            print(f"\n=== {kind} ({len(hits)}) ===")
            for where, detail in hits:
                print(f"   {where}   <-- {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
