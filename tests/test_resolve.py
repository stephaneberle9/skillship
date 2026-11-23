from pathlib import Path

import pytest
from conftest import git, skill_md, write_profile

from skillship import profile
from skillship.errors import NotImplementedYet, SkillshipError
from skillship.resolve import check, closure, resolve
from skillship.source import SourceRepo, glob_to_regex


def resolved(source: Path, text: str):
    write_profile(source, "acme", text)
    repo = SourceRepo(source)
    return repo, resolve(repo, profile.load(source, "acme"))


def test_requires_closure_follows_requires_through_a_cycle(source):
    _, res = resolved(source, "recipient: ACME\ninclude: [billing:invoice-export]\n")
    assert list(res.skills) == [
        "billing:invoice-export",
        "billing:invoice-knowledge",
        "ledger:shared-material",
        "ledger:ledger-knowledge",
        "ledger:ledger-rules",
    ]
    assert res.skills["billing:invoice-export"] == "include billing:invoice-export"
    assert res.skills["ledger:ledger-rules"] == "required by ledger:ledger-knowledge"
    assert res.errors == []


def test_suggests_are_reported_and_not_followed(source):
    _, res = resolved(source, "recipient: ACME\ninclude: [billing:invoice-export]\n")
    assert "ledger:ledger-audit" not in res.skills
    assert res.suggested == {"ledger:ledger-audit": ["billing:invoice-export"]}


def test_a_bare_glob_matches_skill_names_in_any_plugin(source):
    _, res = resolved(source, "recipient: ACME\ninclude: [invoice-*]\n")
    assert {
        "billing:invoice-draft",
        "billing:invoice-export",
        "billing:invoice-knowledge",
    } <= set(res.skills)


def test_files_drop_global_ignores_and_profile_excludes(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [billing:invoice-export]\nfiles:\n  exclude: ['**/tests/**']\n",
    )
    assert res.files["billing:invoice-export"] == ["SKILL.md", "templates/export.md"]
    assert res.files["ledger:shared-material"] == ["README.md", "scripts/setup.sh"]


def test_untracked_files_do_not_ship(source):
    (source / "plugins/billing/skills/invoice-export/notes.md").write_text("draft")
    _, res = resolved(source, "recipient: ACME\ninclude: [billing:invoice-export]\n")
    assert "notes.md" not in res.files["billing:invoice-export"]


def test_license_ships_unless_none(source):
    _, res = resolved(source, "recipient: ACME\ninclude: [ledger:tax-report]\n")
    assert res.extras == ["LICENSE"]
    _, res = resolved(
        source, "recipient: ACME\ninclude: [ledger:tax-report]\nlicense: none\n"
    )
    assert res.extras == []


def test_mentions_of_unshipped_skills_get_a_status(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [billing:invoice-draft, billing:invoice-export]\n",
    )
    found = {(d.ref, d.at.split(":", 1)[1], d.status) for d in res.dangling}
    assert found == {
        # invoice-draft does not suggest ledger-audit, but invoice-export, which ships, does
        ("ledger:ledger-audit", "invoice-draft/SKILL.md:11", "suggested"),
        ("ledger:tax-report", "invoice-draft/SKILL.md:13", "conditional"),
        ("ledger:ledger-audit", "invoice-export/SKILL.md:16", "suggested"),
    }  # templates/export.md describes the recipient's repo and is not scanned


def test_an_unsuggested_unconditional_mention_blocks(source):
    _, res = resolved(source, "recipient: ACME\ninclude: [billing:invoice-draft]\n")
    assert [(d.ref, d.status) for d in res.blocking] == [
        ("ledger:ledger-audit", "blocking")
    ]
    with pytest.raises(
        SkillshipError, match="names `ledger:ledger-audit`, which does not ship"
    ):
        check(res)


def test_acknowledge_accepts_a_mention(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [billing:invoice-draft]\nacknowledge: [ledger:ledger-audit]\n",
    )
    assert [d.status for d in res.dangling if d.ref == "ledger:ledger-audit"] == [
        "acknowledged"
    ]
    check(res)


def test_a_stale_acknowledge_fails(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [ledger:tax-report]\nacknowledge: [ledger:ledger-audit]\n",
    )
    assert res.errors == [
        "`acknowledge: ledger:ledger-audit` matches no dangling mention -- remove it"
    ]


def test_excluding_a_required_skill_names_the_chain(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [billing:invoice-export]\nexclude: [ledger:ledger-rules]\n",
    )
    assert res.errors == [
        (
            "`ledger:ledger-rules` is excluded, but ships: `ledger:ledger-rules` <- "
            "`ledger:ledger-knowledge` <- `billing:invoice-knowledge` <- `billing:invoice-export` "
            "(include billing:invoice-export)"
        )
    ]


def test_patterns_that_match_nothing_fail(source):
    _, res = resolved(
        source,
        "recipient: ACME\ninclude: [billing:nothing-*]\nexclude: [ledger:gone]\n",
    )
    assert res.errors == [
        "`include: billing:nothing-*` matches no skill",
        "`exclude: ledger:gone` matches no skill",
    ]


def test_shape_skills_refuses_two_skills_with_one_name(source):
    other = source / "plugins/other/skills/tax-report"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(skill_md("tax-report"))
    _, res = resolved(source, "recipient: ACME\ninclude: [tax-report]\n")
    assert res.errors == [
        "shape `skills` puts `ledger:tax-report`, `other:tax-report` into the same directory `skills/tax-report/`"
    ]


def test_a_requires_entry_naming_no_skill_fails(source):
    (source / "plugins/ledger/skills/tax-report/SKILL.md").write_text(
        skill_md("tax-report", requires="ghost")
    )
    with pytest.raises(SkillshipError, match="names `ghost`, which is no skill"):
        SourceRepo(source)


def test_closure_lists_what_including_a_skill_would_add(source):
    assert closure(SourceRepo(source), "billing:invoice-knowledge") == [
        "billing:invoice-knowledge",
        "ledger:ledger-knowledge",
        "ledger:ledger-rules",
    ]


def test_root_skills_have_bare_ids(tmp_path):
    (tmp_path / "skills/solo").mkdir(parents=True)
    (tmp_path / "skills/solo/SKILL.md").write_text(skill_md("solo"))
    repo = SourceRepo(tmp_path)  # no git: every file counts
    assert list(repo.skills) == ["solo"]
    assert repo.files(repo.skills["solo"], []) == ["SKILL.md"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("include: [x]\n", "`recipient` is required unless every target is an"),
        (
            "recipient: A\ninclude: []\n",
            "include: []",
        ),  # the rest of the message differs between jsonschema versions
        (
            "recipient: A\ninclude: [x]\nlayout: {shape: plugin}\n",
            "shape `plugin` is now `marketplace` with `merged-plugin",
        ),
        (
            "recipient: A\ninclude: [x]\nlayout: {shape: plugins}\n",
            "shape `plugins` is now `marketplace`",
        ),
        (
            "recipient: A\ninclude: [x]\nlayout: {shape: marketplace, merged-plugin: {}}\n",
            "'name' is a required property",
        ),
        (
            "recipient: A\ninclude: [x]\nlayout: {merged-plugin: {name: m}}\n",
            "'shape' is a required property",
        ),
        (
            "recipient: A\ninclude: [x]\ntargets: [{type: archive, url: u}]\n",
            "Additional properties",
        ),
        (
            "recipient: A\ninclude: [x]\ntargets: [{type: git}]\n",
            "'url' is a required property",
        ),
        ("recipient: [\n", "not valid YAML"),
    ],
)
def test_invalid_profiles_are_refused(source, text, message):
    write_profile(source, "acme", text)
    with pytest.raises(
        SkillshipError, match=message.replace("[", r"\[").replace("]", r"\]")
    ):
        profile.load(source, "acme")


@pytest.mark.parametrize(
    "extra",
    ["license: MIT\n", "license: Apache-2.0\n"],
)
def test_valid_but_unimplemented_profiles_exit_2(source, extra):
    write_profile(source, "acme", "recipient: A\ninclude: [x]\n" + extra)
    with pytest.raises(NotImplementedYet):
        profile.require_implemented(profile.load(source, "acme"))


@pytest.mark.parametrize(
    ("glob", "path", "hit"),
    [
        ("**/tests/**", "tests/a.py", True),
        ("**/tests/**", "x/tests/b/a.py", True),
        ("**/tests/**", "contests/a.py", False),
        ("*.md", "a/b.md", False),
        ("**/*.md", "a/b.md", True),
        ("invoice-?", "invoice-1", True),
    ],
)
def test_glob_to_regex(glob, path, hit):
    assert bool(glob_to_regex(glob).fullmatch(path)) is hit


def test_a_tracked_symlink_is_refused(source):
    # a symlink as git records it (mode 120000), without needing the right to create one
    blob = git(source, "hash-object", "-w", "--stdin", input="/etc/passwd").strip()
    git(source, "update-index", "--add", "--cacheinfo",
        f"120000,{blob},plugins/ledger/skills/tax-report/secrets")  # fmt: skip
    write_profile(source, "acme", "recipient: ACME\ninclude: [ledger:tax-report]\n")
    repo = SourceRepo(source)
    with pytest.raises(SkillshipError, match="tax-report/secrets is a symlink"):
        resolve(repo, profile.load(source, "acme"))


@pytest.mark.parametrize(
    "extra",
    [
        "layout: {root: ../outside}\n",
        "layout: {root: /abs}\n",
        "layout: {root: a/../../b}\n",
        "targets: [{type: archive, name: ..}]\n",
    ],
)
def test_paths_that_leave_the_delivery_are_refused(source, extra):
    write_profile(source, "acme", "recipient: A\ninclude: [x]\n" + extra)
    with pytest.raises(SkillshipError, match="does not match"):
        profile.load(source, "acme")


@pytest.mark.parametrize(
    ("types", "recipient"),
    [
        (
            ["archive"],
            "Early adopters, one-off hand-overs by email, Slack, WhatsApp and the like",
        ),
        (
            ["skill-upload", "plugin-upload"],
            "Early adopters, one-off or repeated uploads into Claude Desktop and claude.ai",
        ),
        (
            ["archive", "plugin-upload"],
            (
                "Early adopters, one-off hand-overs by email, Slack, WhatsApp and the "
                "like, and one-off or repeated uploads into Claude Desktop and claude.ai"
            ),
        ),
    ],
)
def test_a_profile_of_handover_targets_defaults_its_recipient(source, types, recipient):
    targets = "targets:\n" + "".join(f"  - type: {t}\n" for t in types)
    write_profile(source, "acme", "include: [ledger:tax-report]\n" + targets)
    assert profile.load(source, "acme").recipient == recipient


@pytest.mark.parametrize(
    "targets",
    [
        "",
        "targets:\n  - type: archive\n  - type: agensi\n",
    ],
)
def test_a_profile_without_recipient_needs_handover_targets_only(source, targets):
    write_profile(source, "acme", "include: [ledger:tax-report]\n" + targets)
    with pytest.raises(SkillshipError, match="`recipient` is required"):
        profile.load(source, "acme")
