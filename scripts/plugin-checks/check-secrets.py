#!/usr/bin/env python3
"""Find credentials committed to a plugin repo.

Plugin repos are mostly prose, which is exactly why a token pasted into a skill's
example, a setup reference or an `.mcp.json` survives review: it reads as part of
the text. Once pushed it is in the history of every clone, and a marketplace sync
ships it to every user of the plugin -- rotation is the only fix.

  SECRET  A line in a tracked text file that matches a known credential shape:
          GitHub and GitLab tokens, AWS access key ids, Amazon Bedrock API keys,
          Slack tokens, Anthropic API keys, private-key blocks. Placeholders such
          as `ghp_...` or `<token>` do not match -- each pattern needs the real
          length.

Only files tracked by git are read (`git ls-files`), so an ignored `.env` stays
out of it. Repo-specific identifiers (a health insurance number, a customer id)
belong in a check under `scripts/plugin-checks/local/`, not here.

Usage:
    python check-secrets.py [REPO_ROOT]

Exit status: 0 = nothing found, 1 = at least one finding.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

PATTERNS = re.compile(
    r"ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ABSK[A-Za-z0-9+/]{40,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
)
TEXT_SUFFIXES = {".md", ".json", ".jsonc", ".py", ".ps1", ".sh", ".js", ".ts", ".toml", ".txt",
                 ".yml", ".yaml", ".html", ".csv", ".ahk", ".env", ".cfg", ".ini"}


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    try:
        files = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True,
                               check=True, encoding="utf-8").stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"cannot list tracked files under {root} ({exc})")
        return 1

    findings = []
    for name in files:
        path = os.path.join(root, name)
        suffix = os.path.splitext(name)[1].lower()
        if (suffix not in TEXT_SUFFIXES and os.path.basename(name) != ".mcp.json") or not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            match = PATTERNS.search(line)
            if match:
                # Never print the value itself: the output lands in CI logs.
                findings.append(f"   {name}:{number}   <-- looks like a credential ({match.group(0)[:6]}…)")

    print(f"{len(files)} tracked file(s) scanned under {root}\n")
    if not findings:
        print("  OK      no credentials found")
        return 0
    print(f"  SECRET  {len(findings)}\n\n=== SECRET ({len(findings)}) -- revoke and rotate, then remove ===")
    print("\n".join(findings))
    return 1


if __name__ == "__main__":
    sys.exit(main())
