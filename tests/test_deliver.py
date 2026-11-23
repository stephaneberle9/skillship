"""`deliver` to a git target and `drift`, against local bare repositories.

The recipient repo starts with a commit of its own: a README and a marketplace
listing the recipient's plugin `theirs`. Commits are signed with a throwaway
SSH key through an isolated global git configuration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import git, write_profile

from skillship.cli import main
from skillship.config import forge_of
from skillship.deliver import content_hash, inside
from skillship.errors import SkillshipError

GATE = """\
import pathlib, subprocess, sys
target, base = sys.argv[1:]
assert pathlib.Path("distros/skillship.yaml").is_file(), "the gate runs in the source repo"
import os
assert not [k for k in os.environ if k.startswith("DISTRO_")], "the gate gets no credentials"
subprocess.run(["git", "-C", target, "rev-parse", "--verify", base], check=True, capture_output=True)
bad = [p for p in pathlib.Path(target, "plugins").rglob("*") if p.is_file() and "FAIL-GATE" in p.read_text()]
sys.exit(1 if bad else 0)
"""

SETTINGS = f"""\
target-files:
  checks:
    gate: {Path(sys.executable).as_posix()} ci/checks/gate.py {{target}} {{base}}
    files:
      - from: ci/checks/
        to: checks/
        ownership: owned
        rename: {{readme.md: README.md}}
      - from: ci/github.yml
        to: .github/workflows/checks.yml
        when: {{forge: github}}
        ownership: seeded
      - from: ci/gitlab.yml
        to: .gitlab/ci/checks.yml
        when: {{forge: gitlab}}
        ownership: seeded
      - from: ci/hook
        to: .githooks/pre-commit
        mode: "755"
        ownership: seeded
"""

MERGED = "plugins/acme-billing"
KNOWLEDGE = f"{MERGED}/skills/invoice-knowledge/SKILL.md"


def commit(root: Path, message: str = "change") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


@pytest.fixture
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated global git config that signs every `-S` commit with a fresh SSH key."""
    key = tmp_path / "signing-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        f"\tsigningkey = {key.as_posix()}\n"
        "[gpg]\n\tformat = ssh\n[init]\n\tdefaultBranch = main\n"
        "[core]\n\tautocrlf = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def target(tmp_path: Path, signing: None) -> Path:
    """A bare recipient repo with one commit of the recipient's own."""
    work = tmp_path / "recipient"
    files = {
        "README.md": "# ACME plugins\n",
        ".claude-plugin/marketplace.json": json.dumps(
            {
                "name": "acme",
                "owner": {"name": "ACME"},
                "plugins": [{"name": "theirs", "source": "./plugins/theirs"}],
            }
        ),
        "plugins/theirs/.claude-plugin/plugin.json": json.dumps({"name": "theirs"}),
    }
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode())
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    commit(work, "init")
    bare = tmp_path / "recipient.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    return bare


@pytest.fixture
def distro(source: Path, target: Path) -> Path:
    """The `acme` distro as a marketplace with a merged plugin, delivered by push to `target`; source committed."""
    (source / "distros").mkdir(exist_ok=True)
    (source / "distros/skillship.yaml").write_text(SETTINGS, encoding="utf-8")
    for rel, text in {
        "ci/checks/gate.py": GATE,
        "ci/checks/readme.md": "# Checks\n",
        "ci/github.yml": "name: checks\n",
        "ci/gitlab.yml": "checks:\n  script: [true]\n",
        "ci/hook": "#!/bin/sh\nexit 0\n",
    }.items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_bytes(text.encode())
    write_profile(source, "acme", profile(target))
    git(source, "add", "-A")  # skillship delivers git-tracked files only
    assert run(source, "lock", "acme") == 0
    commit(source, "distro")
    return source


def profile(target: Path, extra: str = "", forge: str = "github") -> str:
    return (
        "recipient: ACME\n"
        "include: [billing:invoice-draft]\n"
        "acknowledge: [ledger:ledger-audit]\n"
        "license: none\n"
        "layout:\n  shape: marketplace\n  merged-plugin:\n    name: acme-billing\n    description: ACME billing.\n"
        f"targets:\n  - type: git\n    url: {target.as_uri()}\n    branch: main\n"
        f"    forge: {forge}\n" + extra
    )


def run(source: Path, *argv: str) -> int:
    return main(["-C", str(source), *argv])


def checkout(target: Path, where: Path) -> Path:
    if where.exists():
        git(where, "pull", "-q", "--ff-only")
    else:
        git(target.parent, "clone", "-q", str(target), str(where))
    return where


def record(work: Path) -> dict:
    return json.loads((work / ".skillship.json").read_text(encoding="utf-8"))


def recipient_commit(target: Path, tmp: Path, edit: dict[str, str]) -> None:
    work = checkout(target, tmp / "edit")
    for rel, text in edit.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(text.encode())
    commit(work, "recipient change")
    git(work, "push", "-q", "origin", "HEAD:main")


# -- the lock -----------------------------------------------------------------


def test_the_lock_lists_the_target_files_with_their_ownership(distro: Path):
    lock = (distro / "distros/acme/distro.lock.yaml").read_text(encoding="utf-8")
    assert "target-files:\n  checks:\n    gate: " in lock
    assert (
        "    - path: checks/README.md\n"
        "      from: ci/checks/readme.md\n"
        "      ownership: owned\n"
    ) in lock
    assert "path: .github/workflows/checks.yml" in lock
    assert "forge: github" in lock
    assert ".gitlab/ci/checks.yml" not in lock  # no target on GitLab
    assert (
        "    - path: .githooks/pre-commit\n"
        "      from: ci/hook\n"
        "      ownership: seeded\n"
        "      mode: '755'\n"
    ) in lock


def test_a_profile_opts_out_of_a_set(distro: Path, target: Path):
    write_profile(distro, "acme", profile(target, "target-files: {checks: false}\n"))
    assert run(distro, "lock", "acme") == 0
    assert "target-files" not in (distro / "distros/acme/distro.lock.yaml").read_text()


def test_opting_out_of_an_unknown_set_is_refused(distro: Path, target: Path, capsys):
    write_profile(distro, "acme", profile(target, "target-files: {nope: false}\n"))
    assert run(distro, "lock", "acme") == 1
    assert "names no set of distros/skillship.yaml" in capsys.readouterr().err


def test_no_target_files_without_a_marketplace(distro: Path, target: Path):
    text = profile(target).replace(
        "shape: marketplace\n  merged-plugin:\n    name: acme-billing\n    description: ACME billing.\n",
        "shape: skills\n",
    )
    write_profile(distro, "acme", text)
    assert run(distro, "lock", "acme") == 0
    assert "target-files" not in (distro / "distros/acme/distro.lock.yaml").read_text()


@pytest.mark.parametrize(
    ("target", "forge"),
    [
        ({"url": "https://github.com/acme/skills.git"}, "github"),
        ({"url": "git@github.com:acme/skills.git"}, "github"),
        ({"url": "https://gitlab.com/acme/skills.git"}, "gitlab"),
        ({"url": "https://gitlab.example.com/acme/skills.git"}, "gitlab"),
        ({"url": "https://git.example.com/skills.git"}, None),
        ({"url": "https://git.example.com/skills.git", "forge": "gitlab"}, "gitlab"),
    ],
)
def test_the_forge_comes_from_the_url_or_the_target(target, forge):
    assert forge_of(target) == forge


# -- deliver ------------------------------------------------------------------


def test_deliver_pushes_one_signed_commit_by_ownership(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    assert "pushed to `main`" in capsys.readouterr().out
    work = checkout(target, tmp_path / "check")
    assert "gpgsig" in git(work, "cat-file", "-p", "HEAD")
    assert git(work, "log", "--format=%s", "-1").startswith(
        "chore: deliver acme from source@"
    )

    text = (work / KNOWLEDGE).read_text(encoding="utf-8")
    assert "`/acme-billing:ledger-knowledge`" in text  # flattened
    assert (work / "checks/README.md").read_text() == "# Checks\n"
    assert (work / ".github/workflows/checks.yml").is_file()
    assert not (work / ".gitlab").exists()
    assert git(work, "ls-files", "-s", ".githooks/pre-commit").startswith("100755")

    market = json.loads((work / ".claude-plugin/marketplace.json").read_text())
    assert market["name"] == "acme" and market["owner"] == {"name": "ACME"}
    assert [p["name"] for p in market["plugins"]] == ["theirs", "acme-billing"]

    rec = record(work)
    assert rec["distro"] == "acme"
    assert rec["source"] == "source@" + git(distro, "rev-parse", "HEAD").strip()
    assert rec["plugins"] == ["acme-billing"]
    ownership = {p: m["ownership"] for p, m in rec["files"].items()}
    assert ownership[KNOWLEDGE] == "owned"
    assert ownership["checks/gate.py"] == "owned"
    assert ownership[".github/workflows/checks.yml"] == "seeded"
    assert ownership[".claude-plugin/marketplace.json"] == "merged"
    assert "README.md" not in ownership  # the recipient's own

    assert run(distro, "deliver", "acme") == 0
    assert "up to date, nothing to deliver" in capsys.readouterr().out


def test_dry_run_pushes_nothing(distro: Path, target: Path, capsys):
    before = git(target, "rev-parse", "main")
    assert run(distro, "deliver", "acme", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would deliver to `main` (push)" in out
    assert KNOWLEDGE in out
    assert git(target, "rev-parse", "main") == before


def test_a_failing_gate_stops_the_delivery(distro: Path, target: Path, capsys):
    skill = distro / "plugins/billing/skills/invoice-draft/SKILL.md"
    skill.write_text(skill.read_text() + "\nFAIL-GATE\n", encoding="utf-8")
    commit(distro)
    before = git(target, "rev-parse", "main")
    assert run(distro, "deliver", "acme") == 1
    assert "the `checks` gate failed" in capsys.readouterr().err
    assert git(target, "rev-parse", "main") == before


def test_deliver_refuses_uncommitted_source_changes(distro: Path, capsys):
    skill = distro / "plugins/billing/skills/invoice-draft/SKILL.md"
    skill.write_text(skill.read_text() + "\nmore\n", encoding="utf-8")
    assert run(distro, "deliver", "acme") == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_a_file_that_stops_shipping_is_removed(
    distro: Path, target: Path, tmp_path: Path
):
    assert run(distro, "deliver", "acme") == 0
    git(
        distro, "rm", "-q", "plugins/billing/skills/invoice-knowledge/references/vat.md"
    )
    assert run(distro, "lock", "acme") == 0
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    assert not (work / f"{MERGED}/skills/invoice-knowledge/references").exists()
    assert (
        f"{MERGED}/skills/invoice-knowledge/references/vat.md"
        not in record(work)["files"]
    )


def test_a_seeded_file_stays_the_recipients(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    recipient_commit(target, tmp_path, {".github/workflows/checks.yml": "name: ours\n"})
    (distro / "ci/github.yml").write_text("name: newer\n", encoding="utf-8")
    skill = distro / "plugins/billing/skills/invoice-draft/SKILL.md"
    skill.write_text(skill.read_text() + "\nmore\n", encoding="utf-8")
    commit(distro)
    capsys.readouterr()
    assert run(distro, "deliver", "acme") == 0
    assert (
        "seeded `.github/workflows/checks.yml` has a newer template"
        in capsys.readouterr().out
    )
    work = checkout(target, tmp_path / "check")
    assert (work / ".github/workflows/checks.yml").read_text() == "name: ours\n"


def test_a_newer_seeded_template_is_announced_once_with_its_patch(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    workflow = ".github/workflows/checks.yml"
    assert run(distro, "deliver", "acme") == 0
    recipient_commit(target, tmp_path, {workflow: "# ours\nname: checks\n"})
    (distro / "ci/github.yml").write_text(
        "name: checks\non: [push]  \n", encoding="utf-8"
    )
    commit(distro)

    # nothing else changed: the delivery is a commit for the announcement alone
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    message = git(work, "log", "-1", "--format=%B")
    assert "Seeded files with a newer template" in message
    assert f"{workflow}, from ci/github.yml:" in message
    patch = message[message.index("diff --git") :]
    assert "+on: [push]  \n" in patch  # trailing blanks kept
    (tmp_path / "seeded.patch").write_text(patch, encoding="utf-8", newline="\n")
    # a hunk at line 1 of a file the recipient prepended to: only a 3-way apply fits it
    git(work, "apply", "-3", str(tmp_path / "seeded.patch"))
    assert (work / workflow).read_text() == "# ours\nname: checks\non: [push]  \n"
    git(work, "reset", "-q", "--hard")  # -3 also staged it
    entry = record(work)["files"][workflow]
    assert entry["ownership"] == "seeded" and entry["source-sha256"] != entry["sha256"]

    capsys.readouterr()
    assert run(distro, "deliver", "acme") == 0
    assert "up to date" in capsys.readouterr().out

    # a second change, the first patch never applied: the patch still starts from
    # the seeded version, which the recipient's history holds, and repeats the first
    (distro / "ci/github.yml").write_text(
        "name: checks\non: [push]  \njobs: {}\n", encoding="utf-8"
    )
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    message = git(work, "log", "-1", "--format=%B")
    patch = message[message.index("diff --git") :]
    assert "+on: [push]  \n+jobs: {}\n" in patch
    (tmp_path / "seeded.patch").write_text(patch, encoding="utf-8", newline="\n")
    git(work, "apply", "-3", str(tmp_path / "seeded.patch"))
    assert (work / workflow).read_text() == (
        "# ours\nname: checks\non: [push]  \njobs: {}\n"
    )


HOOKS = """\
      - from: [ci/hooks-base.yaml, ci/hooks-checks.yaml]
        to: .pre-commit-config.yaml
        announce: ci/hooks-checks.yaml
        ownership: seeded
"""


def with_hooks(distro: Path) -> None:
    """Add a seeded `.pre-commit-config.yaml` joined from two files to the settings."""
    settings = distro / "distros/skillship.yaml"
    settings.write_text(settings.read_text() + HOOKS, encoding="utf-8")
    (distro / "ci/hooks-base.yaml").write_text("repos:\n  - repo: base\n")
    (distro / "ci/hooks-checks.yaml").write_text("\n  - repo: checks\n")
    git(distro, "add", "-A")
    assert run(distro, "lock", "acme") == 0
    commit(distro, "hooks")


def test_a_seeded_file_joins_its_sources_in_order(
    distro: Path, target: Path, tmp_path: Path
):
    with_hooks(distro)
    lock = (distro / "distros/acme/distro.lock.yaml").read_text()
    assert "from:\n      - ci/hooks-base.yaml\n      - ci/hooks-checks.yaml\n" in lock
    assert "announce: ci/hooks-checks.yaml" in lock
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    hooks = ".pre-commit-config.yaml"
    assert (work / hooks).read_text() == (
        "repos:\n  - repo: base\n\n  - repo: checks\n"
    )
    assert record(work)["files"][hooks]["ownership"] == "seeded"
    assert "Files you already had" not in git(work, "log", "-1", "--format=%B")

    # a newer part is announced as a patch of the joined file, like any seeded file
    (distro / "ci/hooks-base.yaml").write_text("repos:\n  - repo: base2\n")
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    message = git(work, "log", "-1", "--format=%B")
    assert f"{hooks}, from ci/hooks-base.yaml + ci/hooks-checks.yaml:" in message
    assert "-  - repo: base\n+  - repo: base2\n" in message


def test_a_file_the_recipient_has_gets_the_announcement_once(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    hooks = ".pre-commit-config.yaml"
    recipient_commit(target, tmp_path, {hooks: "repos: []  # ours\n"})
    with_hooks(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    assert (work / hooks).read_text() == "repos: []  # ours\n"
    message = git(work, "log", "-1", "--format=%B")
    assert "Files you already had" in message
    assert f"{hooks}, from ci/hooks-checks.yaml:\n\n  - repo: checks\n" in message
    assert record(work)["files"][hooks]["ownership"] == "announced"

    capsys.readouterr()
    assert run(distro, "deliver", "acme") == 0
    assert "up to date" in capsys.readouterr().out

    (distro / "ci/hooks-checks.yaml").write_text("\n  - repo: checks2\n")
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    assert "  - repo: checks2" in git(work, "log", "-1", "--format=%B")
    assert (work / hooks).read_text() == "repos: []  # ours\n"


def test_only_a_seeded_file_joins_or_announces(distro: Path, capsys):
    settings = distro / "distros/skillship.yaml"
    settings.write_text(
        settings.read_text() + HOOKS.replace("seeded", "owned"), encoding="utf-8"
    )
    (distro / "ci/hooks-base.yaml").write_text("a\n")
    (distro / "ci/hooks-checks.yaml").write_text("b\n")
    git(distro, "add", "-A")
    assert run(distro, "lock", "acme") == 1
    assert "which only a `seeded` file can" in capsys.readouterr().err


def test_announced_notes_fence_text_holding_backticks():
    from skillship.deliver import Announcement, announced_notes

    item = Announcement(".x", "ci/x", b"a ``` b\n")
    notes = announced_notes([item], markdown=True)
    assert "\n````\na ``` b\n````\n" in notes


def test_seeded_notes_fence_a_patch_holding_backticks():
    from skillship.deliver import SeededUpdate, seeded_notes

    update = SeededUpdate("a.md", "t/a.md", b"diff --git a/a.md b/a.md\n+````x\n")
    notes = seeded_notes([update], "src@abc", markdown=True)
    assert "`````diff\n" in notes and notes.rstrip().endswith("`````")


def test_a_versioned_plugin_gets_a_patch_bump_when_it_changes(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    manifest = f"{MERGED}/.claude-plugin/plugin.json"
    work = checkout(target, tmp_path / "edit")
    data = json.loads((work / manifest).read_text())
    data["version"] = "1.0.0"
    recipient_commit(target, tmp_path, {manifest: json.dumps(data, indent=2) + "\n"})

    capsys.readouterr()
    assert (
        run(distro, "deliver", "acme") == 0
    )  # a version is no drift, and nothing changed
    assert "up to date" in capsys.readouterr().out

    skill = distro / "plugins/billing/skills/invoice-draft/SKILL.md"
    skill.write_text(skill.read_text() + "\nmore\n", encoding="utf-8")
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    assert "acme-billing: version 1.0.0 -> 1.0.1" in capsys.readouterr().out
    work = checkout(target, tmp_path / "check")
    assert json.loads((work / manifest).read_text())["version"] == "1.0.1"


def test_version_keep_leaves_the_version(distro: Path, target: Path, tmp_path: Path):
    write_profile(distro, "acme", profile(target, "version: keep\n"))
    assert run(distro, "lock", "acme") == 0
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    manifest = f"{MERGED}/.claude-plugin/plugin.json"
    work = checkout(target, tmp_path / "edit")
    data = json.loads((work / manifest).read_text())
    data["version"] = "2.3.4"
    recipient_commit(target, tmp_path, {manifest: json.dumps(data) + "\n"})
    skill = distro / "plugins/billing/skills/invoice-draft/SKILL.md"
    skill.write_text(skill.read_text() + "\nmore\n", encoding="utf-8")
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    assert json.loads((work / manifest).read_text())["version"] == "2.3.4"


def test_gitlab_gets_a_ci_file_including_the_job(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    write_profile(distro, "acme", profile(target, forge="gitlab"))
    assert run(distro, "lock", "acme") == 0
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    assert (
        work / ".gitlab-ci.yml"
    ).read_text() == "include:\n  - local: .gitlab/ci/checks.yml\n"
    assert record(work)["files"][".gitlab-ci.yml"]["ownership"] == "seeded"
    assert not (work / ".github").exists()


def test_a_recipients_gitlab_ci_file_is_never_edited(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    recipient_commit(target, tmp_path, {".gitlab-ci.yml": "stages: [test]\n"})
    write_profile(distro, "acme", profile(target, forge="gitlab"))
    assert run(distro, "lock", "acme") == 0
    commit(distro)
    assert run(distro, "deliver", "acme") == 0
    assert "- local: .gitlab/ci/checks.yml" in capsys.readouterr().out
    work = checkout(target, tmp_path / "check")
    assert (work / ".gitlab-ci.yml").read_text() == "stages: [test]\n"


def test_a_target_holding_another_distro_is_refused(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    recipient_commit(
        target,
        tmp_path,
        {".skillship.json": json.dumps({"distro": "other", "files": {}})},
    )
    assert run(distro, "deliver", "acme") == 1
    assert "holds the delivery of distro `other`" in capsys.readouterr().err


def test_merge_request_mode_needs_gitlab(distro: Path, target: Path, capsys):
    write_profile(
        distro,
        "acme",
        profile(target).replace(
            "    forge: github\n", "    forge: github\n    mode: merge-request\n"
        ),
    )
    assert run(distro, "lock", "acme") == 0
    commit(distro)
    assert run(distro, "deliver", "acme") == 1
    assert "needs a GitLab target" in capsys.readouterr().err


# -- drift --------------------------------------------------------------------


def test_drift_stops_a_delivery_and_merges_back_through_the_flattening(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "edit")
    text = (work / KNOWLEDGE).read_text(encoding="utf-8")
    edited = text.replace("Post entries with", "Always post entries with")
    assert edited != text
    recipient_commit(target, tmp_path, {KNOWLEDGE: edited})

    capsys.readouterr()
    assert run(distro, "deliver", "acme") == 1
    err = capsys.readouterr().err
    assert f"{KNOWLEDGE} (modified)" in err
    assert "skillship drift acme" in err

    assert run(distro, "drift", "acme") == 1
    out = capsys.readouterr().out
    assert "clean" in out and KNOWLEDGE in out, out
    src = "plugins/billing/skills/invoice-knowledge/SKILL.md"
    patch = distro / "dist/acme/drift" / f"{src}.patch"
    assert patch.is_file()
    git(distro, "apply", str(patch))
    merged = (distro / src).read_text(encoding="utf-8")
    assert (
        "Always post entries with `/ledger:ledger-knowledge`." in merged
    )  # not flattened
    assert git(distro, "diff", "--stat").count("|") == 1

    git(distro, "checkout", "-q", "--", src)
    assert run(distro, "deliver", "acme", "--overwrite", KNOWLEDGE) == 0
    capsys.readouterr()
    assert run(distro, "drift", "acme") == 0
    assert "no drift" in capsys.readouterr().out


def test_a_change_taken_back_no_longer_stops_a_delivery(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "edit")
    text = (work / KNOWLEDGE).read_text(encoding="utf-8")
    recipient_commit(
        target,
        tmp_path,
        {KNOWLEDGE: text.replace("Post entries with", "Always post entries with")},
    )
    assert run(distro, "drift", "acme") == 1
    src = "plugins/billing/skills/invoice-knowledge/SKILL.md"
    git(distro, "apply", str(distro / "dist/acme/drift" / f"{src}.patch"))
    commit(distro)
    capsys.readouterr()
    assert run(distro, "deliver", "acme") == 0, capsys.readouterr().err
    assert run(distro, "drift", "acme") == 0
    assert "no drift" in capsys.readouterr().out


def test_drift_merges_onto_a_source_that_moved_on(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "edit")
    text = (work / KNOWLEDGE).read_text(encoding="utf-8")
    recipient_commit(
        target,
        tmp_path,
        {KNOWLEDGE: text.replace("Post entries with", "Always post entries with")},
    )
    src = distro / "plugins/billing/skills/invoice-knowledge/SKILL.md"
    src.write_text(
        src.read_text(encoding="utf-8") + "\nA new closing line.\n", encoding="utf-8"
    )
    commit(distro)
    assert run(distro, "drift", "acme") == 1
    patch = (
        distro
        / "dist/acme/drift/plugins/billing/skills/invoice-knowledge/SKILL.md.patch"
    )
    git(distro, "apply", "--check", str(patch))


def test_drift_reports_a_deleted_and_a_generated_file(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "edit")
    git(work, "rm", "-q", "checks/README.md")
    (work / f"{MERGED}/README.md").write_text("# Ours\n", encoding="utf-8")
    commit(work)
    git(work, "push", "-q", "origin", "HEAD:main")
    capsys.readouterr()
    assert run(distro, "drift", "acme") == 1
    out = capsys.readouterr().out
    assert "deleted" in out and "checks/README.md" in out
    assert "generated" in out and f"{MERGED}/README.md" in out


def test_a_symlink_in_the_target_is_never_followed(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    outside = tmp_path / "outside"
    outside.mkdir()
    git(tmp_path, "config", "--global", "core.symlinks", "true")
    work = checkout(target, tmp_path / "edit")
    try:
        (work / "checks").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create symlinks")
    commit(work, "a symlink out of the repo")
    git(work, "push", "-q", "origin", "HEAD:main")
    assert run(distro, "deliver", "acme") == 1
    assert "passes through a symlink" in capsys.readouterr().err
    assert not any(outside.iterdir())


def test_a_forged_record_entry_never_deletes_a_recipient_file(
    distro: Path, target: Path, tmp_path: Path, capsys
):
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "edit")
    forged = record(work)
    readme = (work / "README.md").read_bytes()
    forged["files"]["README.md"] = {
        "ownership": "owned",
        "sha256": content_hash("README.md", readme),
    }
    recipient_commit(
        target, tmp_path, {".skillship.json": json.dumps(forged, indent=2) + "\n"}
    )
    assert run(distro, "deliver", "acme") == 0
    assert "`README.md` no longer ships, but the source does not confirm" in (
        capsys.readouterr().out
    )
    work = checkout(target, tmp_path / "check")
    assert (work / "README.md").read_bytes() == readme
    assert "README.md" not in record(work)["files"]


@pytest.mark.parametrize("path", [".GIT/config", ".git./config", "a/.git /x", ".git"])
def test_a_path_into_git_is_refused_in_any_spelling(tmp_path: Path, path: str):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("")
    with pytest.raises(SkillshipError, match="leaves the target repo"):
        inside(tmp_path, path)


@pytest.mark.parametrize(
    "forged", ["HEAD", "HEAD~1", "--orphan", "other@{sha}", "source@{short}"]
)
def test_a_record_naming_no_exact_ancestor_rebuilds_nothing(
    distro: Path, target: Path, tmp_path: Path, forged: str
):
    from skillship import profile as profile_mod
    from skillship.deliver import base_delivery
    from skillship.source import SourceRepo

    sha = git(distro, "rev-parse", "HEAD").strip()
    repo = SourceRepo(distro)
    source = forged.format(sha=sha, short=sha[:12]).replace("source", repo.name())
    record_ = {"source": source if "@" in source else f"{repo.name()}@{source}"}
    prof = profile_mod.load(distro, "acme")
    assert base_delivery(repo, prof, record_, "github", tmp_path / "base") is None
    assert base_delivery(
        repo, prof, {"source": f"{repo.name()}@{sha}"}, "github", tmp_path / "ok"
    )


# -- CI credentials -----------------------------------------------------------


def ci_env(monkeypatch: pytest.MonkeyPatch, key: str, passphrase: str) -> None:
    monkeypatch.setenv("DISTRO_SIGNING_KEY", key)
    monkeypatch.setenv("DISTRO_SIGNING_KEY_PASSPHRASE", passphrase)
    monkeypatch.setenv("DISTRO_COMMITTER_NAME", "Distro Bot")
    monkeypatch.setenv("DISTRO_COMMITTER_EMAIL", "bot@example.invalid")


def test_ci_signs_with_a_passphrase_protected_ssh_key(
    distro: Path, target: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    key = tmp_path / "ci-key"
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "s3cret",
            "-C",
            "ci",
            "-f",
            str(key),
        ],
        check=True,
    )
    ci_env(monkeypatch, key.read_text(), "s3cret")
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    raw = git(work, "cat-file", "commit", "HEAD")
    assert "-----BEGIN SSH SIGNATURE-----" in raw
    assert "author Distro Bot <bot@example.invalid>" in raw
    signers = tmp_path / "allowed"
    signers.write_text(f"bot@example.invalid {key.with_suffix('.pub').read_text()}")
    git(
        work,
        "-c",
        f"gpg.ssh.allowedSignersFile={signers.as_posix()}",
        "verify-commit",
        "HEAD",
    )


def test_ci_signs_with_a_passphrase_protected_gpg_key(
    distro: Path, target: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # short: the gpg-agent socket lives in it, and its path has a length limit
    home = Path(tempfile.mkdtemp(prefix="gpg-"))
    from skillship.ci import gpg_home

    env = {**os.environ, "GNUPGHOME": gpg_home(home)}
    gpg = ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "s3cret"]
    try:
        subprocess.run(
            [*gpg, "--quick-gen-key", "Distro Bot <bot@example.invalid>", "ed25519", "sign", "never"],
            env=env, check=True, capture_output=True,
        )  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("gpg cannot generate a key here")
    secret = subprocess.run(
        [*gpg, "--armor", "--export-secret-keys"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False)
    ci_env(monkeypatch, secret, "s3cret")
    assert run(distro, "deliver", "acme") == 0
    work = checkout(target, tmp_path / "check")
    raw = git(work, "cat-file", "commit", "HEAD")
    assert "-----BEGIN PGP SIGNATURE-----" in raw
    assert "committer Distro Bot <bot@example.invalid>" in raw
    done = subprocess.run(
        ["git", "-C", str(work), "verify-commit", "HEAD"],
        env=env,
        capture_output=True,
        check=False,
    )
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False)
    assert done.returncode == 0, done.stderr


def test_ci_needs_a_committer_for_its_key(
    distro: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    # split, so the plugin checks' secret scan does not take the fake header for a key
    fake_key = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nx\n"
    monkeypatch.setenv("DISTRO_SIGNING_KEY", fake_key)
    assert run(distro, "deliver", "acme") == 1
    assert (
        "DISTRO_COMMITTER_NAME or DISTRO_COMMITTER_EMAIL is not"
        in capsys.readouterr().err
    )


def test_ci_refuses_an_unknown_key(
    distro: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    ci_env(monkeypatch, "not a key", "")
    assert run(distro, "deliver", "acme") == 1
    assert "neither an OpenSSH private key" in capsys.readouterr().err


def test_ci_token_goes_to_git_as_a_header_for_the_target_host(
    monkeypatch: pytest.MonkeyPatch,
):
    import base64

    from skillship import ci, gitops

    monkeypatch.setenv("DISTRO_DEPLOY_TOKEN", "t0ken")
    with ci.credentials(
        "https://gitlab.example.com/acme/plugins.git", "gitlab"
    ) as args:
        env = dict(gitops.EXTRA_ENV)
    assert args == [] and not gitops.EXTRA_ENV
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.com/.extraHeader"
    assert (
        env["GIT_CONFIG_VALUE_0"]
        == "Authorization: Basic " + base64.b64encode(b"oauth2:t0ken").decode()
    )
    assert env["GITLAB_TOKEN"] == "t0ken" and env["GITLAB_HOST"] == "gitlab.example.com"
    with ci.credentials("https://github.com/acme/plugins.git", "github"):
        assert gitops.EXTRA_ENV["GH_TOKEN"] == "t0ken"
        assert (
            "x-access-token"
            in base64.b64decode(
                gitops.EXTRA_ENV["GIT_CONFIG_VALUE_0"].split()[-1]
            ).decode()
        )
    with (
        pytest.raises(SkillshipError, match="authenticates over HTTPS"),
        ci.credentials("git@github.com:acme/plugins.git", "github"),
    ):
        pass


def test_ci_opens_a_merge_request_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from skillship import gitops

    git(tmp_path, "init", "-q", str(tmp_path / "c"))
    clone = tmp_path / "c"
    git(
        clone,
        "remote",
        "add",
        "origin",
        "https://gitlab.example.com/acme/sub/plugins.git",
    )
    calls = []

    def fake(url, method, data=None):
        calls.append((method, url, data))
        if method == "GET":  # a fork's merge request from a branch of the same name
            return [{"iid": 9, "project_id": 1, "source_project_id": 2}]
        return {"web_url": "https://mr/1"}

    monkeypatch.setattr(gitops, "_gitlab", fake)
    monkeypatch.setitem(gitops.EXTRA_ENV, "GITLAB_TOKEN", "t")
    url = gitops.open_pull_request(clone, "gitlab", "main", "skillship/acme", "T", "B")
    assert url == "https://mr/1"
    api = (
        "https://gitlab.example.com/api/v4/projects/acme%2Fsub%2Fplugins/merge_requests"
    )
    assert calls[0][0] == "GET" and calls[0][1].startswith(api + "?")
    assert calls[1] == (
        "POST",
        api,
        {
            "source_branch": "skillship/acme",
            "target_branch": "main",
            "title": "T",
            "description": "B",
            "remove_source_branch": True,
        },
    )
