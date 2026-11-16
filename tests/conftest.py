"""A synthetic source repo: two plugins, shared material, a cycle and dangling mentions.

billing:invoice-knowledge      requires ledger:ledger-knowledge
billing:invoice-export         requires invoice-knowledge, ledger:shared-material
                               suggests ledger:ledger-audit
billing:invoice-draft          requires invoice-knowledge; names `/ledger:ledger-audit`
ledger:ledger-knowledge        requires ledger-rules        (cycle)
ledger:ledger-rules            requires ledger-knowledge    (cycle)
ledger:ledger-audit            names "the `tax-report` skill" (dangling when shipped)
ledger:tax-report
ledger:shared-material         scripts/setup.sh (executable)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def skill_md(name: str, requires: str = "", suggests: str = "", body: str = "") -> str:
    meta = ""
    if requires or suggests:
        meta = "metadata:\n"
        if requires:
            meta += "  requires: >-\n" + "".join(f"    {r}\n" for r in requires.split())
        if suggests:
            meta += "  suggests: >-\n" + "".join(f"    {s}\n" for s in suggests.split())
    return f"---\nname: {name}\ndescription: The {name} skill.\n{meta}---\n\n# {name}\n\n{body}\n"


FIXTURE = {
    "LICENSE": "MIT License\n",
    "plugins/billing/skills/invoice-knowledge/SKILL.md": skill_md(
        "invoice-knowledge", requires="ledger:ledger-knowledge"
    ),
    "plugins/billing/skills/invoice-knowledge/references/vat.md": "# VAT\r\n\r\nRates.\r\n",
    "plugins/billing/skills/invoice-export/SKILL.md": skill_md(
        "invoice-export",
        requires="invoice-knowledge ledger:shared-material",
        suggests="ledger:ledger-audit",
        body="Run `<skill-dir>/../shared-material/scripts/setup.sh`.\n\n"
        "Then audit with the `ledger-audit` skill.",
    ),
    "plugins/billing/skills/invoice-export/templates/export.md": "Use `/ledger:tax-report` in your repo.\n",
    "plugins/billing/skills/invoice-export/tests/test_export.py": "def test(): pass\n",
    "plugins/billing/skills/invoice-draft/SKILL.md": skill_md(
        "invoice-draft",
        requires="invoice-knowledge",
        body="Hand the draft to `/ledger:ledger-audit`.\n\n"
        "Check it with `/ledger:tax-report`, when installed.",
    ),
    "plugins/ledger/skills/ledger-knowledge/SKILL.md": skill_md(
        "ledger-knowledge", requires="ledger-rules"
    ),
    "plugins/ledger/skills/ledger-rules/SKILL.md": skill_md(
        "ledger-rules", requires="ledger-knowledge"
    ),
    "plugins/ledger/skills/ledger-audit/SKILL.md": skill_md(
        "ledger-audit", body="File the result with the `tax-report` skill."
    ),
    "plugins/ledger/skills/tax-report/SKILL.md": skill_md("tax-report"),
    "plugins/ledger/skills/shared-material/README.md": "# Shared\n",
    "plugins/ledger/skills/shared-material/scripts/setup.sh": "#!/bin/sh\necho setup\n",
    "plugins/ledger/skills/shared-material/scripts/__pycache__/x.pyc": "junk",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """The fixture as a git repo with one commit (a throwaway repo, so unsigned)."""
    root = tmp_path / "source"
    for rel, text in FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    git(root.parent, "init", "-q", "-b", "main", str(root))
    git(root, "add", "-A")
    git(
        root,
        "update-index",
        "--chmod=+x",
        "plugins/ledger/skills/shared-material/scripts/setup.sh",
    )
    git(
        root,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "fixture",
    )  # fmt: skip
    return root


def write_profile(source: Path, name: str, text: str) -> Path:
    path = source / "distros" / name / "distro.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
