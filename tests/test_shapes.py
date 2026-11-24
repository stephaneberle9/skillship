"""The layouts, the MCP extras, the README, and the rewrites."""

import json
import zipfile
from types import SimpleNamespace

import pytest
from conftest import git, skill_md, write_profile

from skillship import profile
from skillship.cli import main
from skillship.errors import SkillshipError
from skillship.resolve import resolve
from skillship.rewrite import Rewriter
from skillship.source import SourceRepo

INCLUDE = "recipient: ACME\ninclude: [billing:invoice-export]\nlicense: none\n"


def target(layout):
    return f"targets: [{{type: archive, layout: {layout}}}]\n"


def build(source, text, layout="{shape: skills}"):
    """The files of the one archive a profile of `text` builds in `layout`."""
    write_profile(source, "acme", text + target(layout))
    assert main(["-C", str(source), "lock", "acme"]) == 0
    assert main(["-C", str(source), "build", "acme"]) == 0
    (archive,) = (source / "dist/acme").glob("*.zip")
    zf = zipfile.ZipFile(archive)
    return {n: zf.read(n).decode() for n in zf.namelist()}


def resolved(source, text, layout="{shape: skills}"):
    """What a profile of `text` resolves to in `layout`: the shared errors and that layout's."""
    write_profile(source, "acme", text + target(layout))
    repo = SourceRepo(source)
    res = resolve(repo, profile.load(source, "acme"))
    (var,) = res.variants
    return SimpleNamespace(
        errors=res.errors + var.errors,
        plugins=var.plugins,
        dead_links=var.dead_links,
        uploads=var.uploads,
    )


# -- flattening --------------------------------------------------------------

SKILLS_OF = {"ledger": {"ledger-knowledge", "ledger-audit"}, "billing": {"invoice"}}
SHIPPED = {"ledger:ledger-knowledge", "billing:invoice"}
FLAT = Rewriter(SKILLS_OF, SHIPPED, True)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Run `/ledger:ledger-knowledge`.", "Run `/ledger-knowledge`."),
        ("Load `ledger:ledger-knowledge` first.", "Load `ledger-knowledge` first."),
        (
            "the `ledger-knowledge` skill (`ledger` plugin).",
            "the `ledger-knowledge` skill.",
        ),
        (
            "(see the `ledger-knowledge` skill, `ledger` plugin)",
            "(see the `ledger-knowledge` skill)",
        ),
        (
            "the ledger-knowledge skill\n(ledger plugin) holds it",
            "the ledger-knowledge skill holds it",
        ),
        # the house's phrasings for a reference against the dependency direction
        (
            "the `ledger-knowledge` skill (`ledger` plugin, when installed).",
            "the `ledger-knowledge` skill.",
        ),
        ("Run `ledger-knowledge` (`ledger`) first.", "Run `ledger-knowledge` first."),
        (
            "`ledger-knowledge`, from the `ledger` plugin when installed |",
            "`ledger-knowledge` |",
        ),
        (
            "the `ledger-knowledge` skill, from the ledger plugin, if installed.",
            "the `ledger-knowledge` skill.",
        ),
        # a skill that does not ship keeps its upstream name and attribution
        ("Run `/ledger:ledger-audit`.", "Run `/ledger:ledger-audit`."),
        (
            "the `ledger-audit` skill (`ledger` plugin)",
            "the `ledger-audit` skill (`ledger` plugin)",
        ),
        # a plugin with no shipped skill is no source plugin of the delivery
        (
            "`other:thing` and the `other` plugin",
            "`other:thing` and the `other` plugin",
        ),
        ("ledger-knowledge and subledger stay", "ledger-knowledge and subledger stay"),
    ],
)
def test_flattening_for_shape_skills(before, after):
    out = FLAT.rewrite(before)
    assert out.text == after
    assert out.unflattened == []


def test_shape_skills_drops_the_plugin_from_a_pointer_to_its_own_file():
    text = (
        "See this plugin's `CONNECTORS.md`. This plugin's\n"
        "[README](../../README.md) lists them. This plugin's skills stay.\n"
    )
    out = FLAT.rewrite(text)
    assert out.text == (
        "See the `CONNECTORS.md`. The\n"
        "[README](../../README.md) lists them. This plugin's skills stay.\n"
    )
    assert out.count == 2
    # with a merged plugin the skill is in a plugin, and the wording holds
    merged = Rewriter(SKILLS_OF, SHIPPED, True, "acme")
    assert merged.rewrite(text).text == text
    assert Rewriter(SKILLS_OF, SHIPPED, False).rewrite(text).text == text


def test_flattening_for_a_merged_marketplace_names_the_merged_plugin():
    flat = Rewriter(
        {"ledger": {"ledger-knowledge"}}, {"ledger:ledger-knowledge"}, True, "acme"
    )
    text = (
        "---\nname: x\nmetadata:\n  requires: ledger:ledger-knowledge\n---\n\n"
        "Run `/ledger:ledger-knowledge`, the `ledger-knowledge` skill (`ledger` plugin).\n"
    )
    out = flat.rewrite(text, "ledger:x", plugin="ledger")
    assert out.text == (
        "---\nname: x\nmetadata:\n  requires: ledger-knowledge\n---\n\n"
        "Run `/acme:ledger-knowledge`, the `ledger-knowledge` skill (`acme` plugin).\n"
    )
    assert out.count == 3
    assert [r.line for r in out.rewrites] == [4, 7]


def front(meta):
    return f"---\nname: invoice\n{meta}---\n\nBody.\n"


@pytest.mark.parametrize(
    ("flattening", "before", "after"),
    [
        # unshipped entries go; a shipped one stays, flattened where the layout flattens
        (
            True,
            "metadata:\n  suggests: >-\n    ledger:ledger-audit\n    ledger:ledger-knowledge\n",
            "metadata:\n  suggests: >-\n    ledger-knowledge\n",
        ),
        (
            False,
            "metadata:\n  suggests: >-\n    ledger:ledger-audit\n    ledger:ledger-knowledge\n",
            "metadata:\n  suggests: >-\n    ledger:ledger-knowledge\n",
        ),
        # nothing left: the key goes, and `metadata` with it when it held nothing else
        (False, "metadata:\n  suggests: ledger:ledger-audit\n", ""),
        (
            False,
            "metadata:\n  requires: invoice\n  suggests: ledger:ledger-audit\n",
            "metadata:\n  requires: invoice\n",
        ),
        (
            False,
            "metadata:\n  suggests: ledger:ledger-audit\n  other: x\n",
            "metadata:\n  other: x\n",
        ),
        # a bare entry is a skill of the skill's own plugin
        (
            False,
            "metadata:\n  suggests: invoice gone\n",
            "metadata:\n  suggests: invoice\n",
        ),
    ],
)
def test_suggests_keeps_only_what_ships(flattening, before, after):
    skills_of = {**SKILLS_OF, "billing": {"invoice", "gone"}}
    out = Rewriter(skills_of, SHIPPED, flattening).rewrite(
        front(before), "billing:invoice", plugin="billing"
    )
    assert out.text == front(after)


def test_suggests_is_not_pruned_outside_a_skill_md():
    text = front("metadata:\n  suggests: ledger:ledger-audit\n")
    assert Rewriter(SKILLS_OF, SHIPPED, False).rewrite(text).text == text


def test_other_mentions_of_a_source_plugin_are_unflattened():
    out = FLAT.rewrite(
        "Intro.\n\nSee the `ledger` plugin's README.\n\n(`ledger` plugin) alone.\n"
    )
    assert out.unflattened == [("ledger", 3), ("ledger", 5)]
    assert out.count == 0


# -- shape marketplace -------------------------------------------------------


def test_shape_marketplace_delivers_the_source_plugins_with_trimmed_manifests(source):
    files = build(source, INCLUDE, "{shape: marketplace, marketplace: acme-market}")
    assert json.loads(files[".claude-plugin/marketplace.json"]) == {
        "name": "acme-market",
        "owner": {"name": "Fixture Owner"},
        "plugins": [
            {
                "name": "billing",
                "description": "Billing skills.",
                "source": "./plugins/billing",
            },
            {
                "name": "ledger",
                "description": "Ledger skills.",
                "source": "./plugins/ledger",
            },
        ],
    }
    # repository dropped, license dropped (`license: none`), dependencies kept
    assert json.loads(files["plugins/billing/.claude-plugin/plugin.json"]) == {
        "name": "billing",
        "description": "Billing skills.",
        "author": {"name": "Fixture Owner"},
        "dependencies": ["ledger", "outside@other-market"],
    }
    assert "plugins/billing/skills/invoice-export/SKILL.md" in files
    assert "plugins/ledger/skills/shared-material/scripts/setup.sh" in files
    # no flattening: the plugins exist
    assert (
        "`/ledger:ledger-knowledge`"
        in files["plugins/billing/skills/invoice-knowledge/SKILL.md"]
    )
    record = json.loads(files[".skillship.json"])["files"]
    assert record[".claude-plugin/marketplace.json"]["ownership"] == "merged"
    assert record["plugins/billing/README.md"]["ownership"] == "owned"


def test_shape_marketplace_readme_lists_the_shipped_skills_only(source):
    readme = build(source, INCLUDE, "{shape: marketplace}")["plugins/billing/README.md"]
    assert readme.startswith("# billing\n\nBilling skills.\n\n## Skills\n")
    assert (
        "| [`/invoice-export`](skills/invoice-export/SKILL.md) | The invoice-export skill. |"
        in readme
    )
    assert "invoice-draft" not in readme
    assert "- [`ledger`](../ledger/README.md)\n- `outside@other-market`\n" in readme


def test_background_skills_get_their_own_table(source):
    path = source / "plugins/ledger/skills/ledger-rules/SKILL.md"
    path.write_text(
        path.read_text().replace(
            "name: ledger-rules\n", "name: ledger-rules\nuser-invocable: false\n"
        )
    )
    readme = build(source, INCLUDE, "{shape: marketplace}")["plugins/ledger/README.md"]
    background = readme.split("## Background Skills")[1]
    assert "[`ledger-rules`](skills/ledger-rules/SKILL.md)" in background
    assert "/ledger-knowledge" in readme.split("## Background Skills")[0]


def test_inherit_keeps_the_manifest_license(source):
    files = build(
        source,
        INCLUDE.replace("license: none\n", ""),
        "{shape: marketplace}",
    )
    assert (
        json.loads(files["plugins/ledger/.claude-plugin/plugin.json"])["license"]
        == "MIT"
    )
    assert "LICENSE" in files


def test_a_dependency_on_an_unshipped_plugin_becomes_what_it_brings_along(source):
    bridge = source / "plugins/bridge"
    (bridge / ".claude-plugin").mkdir(parents=True)
    (bridge / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "bridge", "dependencies": ["ledger"]})
    )
    (bridge / "skills/hop").mkdir(parents=True)
    (bridge / "skills/hop/SKILL.md").write_text(skill_md("hop"))
    manifest = source / "plugins/billing/.claude-plugin/plugin.json"
    manifest.write_text(json.dumps({"name": "billing", "dependencies": ["bridge"]}))
    git(source, "add", "-A")
    res = resolved(source, INCLUDE, "{shape: marketplace}")
    assert [(p.name, p.dependencies) for p in res.plugins] == [
        ("billing", ["ledger"]),
        ("ledger", []),
    ]


def test_shape_marketplace_needs_a_plugin_for_root_skills(tmp_path):
    (tmp_path / "skills/solo").mkdir(parents=True)
    (tmp_path / "skills/solo/SKILL.md").write_text(skill_md("solo"))
    res = resolved(tmp_path, "recipient: A\ninclude: [solo]\n", "{shape: marketplace}")
    assert any(
        "root skills have no `.claude-plugin/plugin.json`" in e for e in res.errors
    )
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "solo-plugin"})
    )
    res = resolved(tmp_path, "recipient: A\ninclude: [solo]\n", "{shape: marketplace}")
    assert res.errors == []
    assert [p.name for p in res.plugins] == ["solo-plugin"]


def test_readme_false_is_refused_in_a_marketplace(source):
    write_profile(
        source,
        "acme",
        INCLUDE + "plugin-extras: {readme: false}\n" + target("{shape: marketplace}"),
    )
    with pytest.raises(SkillshipError, match="always generates a README"):
        resolve(SourceRepo(source), profile.load(source, "acme"))


# -- shape plugin ------------------------------------------------------------


def test_shape_plugin_is_one_flattened_plugin_at_the_root(source):
    files = build(
        source,
        INCLUDE + "plugin-extras: {mcp: true}\n",
        "{shape: plugin, name: acme-billing, description: Billing for ACME.}",
    )
    assert json.loads(files[".claude-plugin/plugin.json"]) == {
        "name": "acme-billing",
        "description": "Billing for ACME.",
        "author": {"name": "Fixture Owner"},
        "dependencies": ["outside@other-market"],
    }
    assert ".claude-plugin/marketplace.json" not in files
    assert files["README.md"].startswith("# acme-billing\n\nBilling for ACME.\n")
    assert "[`/invoice-export`](skills/invoice-export/SKILL.md)" in files["README.md"]
    knowledge = files["skills/invoice-knowledge/SKILL.md"]
    assert "`/acme-billing:ledger-knowledge`" in knowledge
    assert "the `ledger-rules` skill (`acme-billing` plugin)" in knowledge
    assert {".mcp.json", "CONNECTORS.md", "connectors/books.env"} <= set(files)


def test_shape_plugin_defaults_its_name_and_description(source):
    files = build(source, INCLUDE, "{shape: plugin}")
    manifest = json.loads(files[".claude-plugin/plugin.json"])
    assert manifest["name"] == "acme"
    assert manifest["description"] == (
        "The skill `invoice-export`, with the skills it requires."
    )


def test_shape_plugin_lists_a_path_that_leaves_the_plugin(source):
    path = source / "plugins/billing/skills/invoice-export/notes.md"
    path.write_text("Read `../../../ledger/skills/tax-report/SKILL.md` first.\n")
    git(source, "add", "-A")
    res = resolved(source, INCLUDE, "{shape: plugin}")
    assert [(u.target, u.at, u.path) for u in res.uploads.unresolved] == [
        (
            "plugin",
            "billing:invoice-export/notes.md:1",
            "../../../ledger/skills/tax-report/SKILL.md",
        )
    ]


def test_shape_plugin_is_not_a_git_layout_yet(source, capsys):
    write_profile(
        source,
        "acme",
        INCLUDE
        + "targets: [{type: git, url: https://example.invalid/r.git, "
        + "layout: {shape: plugin}}]\n",
    )
    assert main(["-C", str(source), "lock", "acme"]) == 2
    assert "shape `plugin` in a `git` target" in capsys.readouterr().err


# -- a flattened marketplace -------------------------------------------------


def test_flatten_merges_everything_into_one_flattened_plugin(source):
    files = build(
        source,
        INCLUDE,
        "{shape: marketplace, flatten: true, plugin-name: acme-skills, "
        "plugin-description: ACME's skills.}",
    )
    assert json.loads(files["plugins/acme-skills/.claude-plugin/plugin.json"]) == {
        "name": "acme-skills",
        "description": "ACME's skills.",
        "author": {"name": "Fixture Owner"},
        "dependencies": ["outside@other-market"],
    }
    assert [
        p["name"]
        for p in json.loads(files[".claude-plugin/marketplace.json"])["plugins"]
    ] == ["acme-skills"]
    knowledge = files["plugins/acme-skills/skills/invoice-knowledge/SKILL.md"]
    assert "    ledger-knowledge\n" in knowledge  # metadata.requires loses its prefix
    assert "`/acme-skills:ledger-knowledge`" in knowledge
    assert "the `ledger-rules` skill (`acme-skills` plugin)" in knowledge
    assert "plugins/acme-skills/skills/shared-material/scripts/setup.sh" in files
    assert "plugins/acme-skills/README.md" in files


def test_flatten_defaults_the_plugin_to_the_distro_name(source):
    files = build(source, INCLUDE, "{shape: marketplace, flatten: true}")
    assert "plugins/acme/skills/invoice-export/SKILL.md" in files
    manifest = json.loads(files["plugins/acme/.claude-plugin/plugin.json"])
    assert manifest["description"].startswith("The skill `invoice-export`")


def test_flatten_refuses_two_skills_with_one_name(source):
    other = source / "plugins/other/skills/tax-report"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(skill_md("tax-report"))
    res = resolved(
        source,
        "recipient: A\ninclude: [tax-report]\n",
        "{shape: marketplace, flatten: true, plugin-name: m}",
    )
    assert res.errors == [
        (
            "`flatten` puts `ledger:tax-report`, `other:tax-report` into the same "
            "directory `plugins/m/skills/tax-report/`"
        )
    ]


# -- shape skills ------------------------------------------------------------


def test_shape_skills_flattens_and_can_add_a_readme(source):
    files = build(source, INCLUDE + "plugin-extras: {readme: true}\n")
    knowledge = files["skills/invoice-knowledge/SKILL.md"]
    assert "`/ledger-knowledge`" in knowledge
    assert "the `ledger-rules` skill." in knowledge
    assert files["README.md"].startswith("# Skills\n\n## Skills\n")
    assert "[`/invoice-export`](skills/invoice-export/SKILL.md)" in files["README.md"]


# -- dead links --------------------------------------------------------------


def link_from_ledger_knowledge(source, body):
    path = source / "plugins/ledger/skills/ledger-knowledge/references/links.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(body)
    git(source, "add", "-A")


def test_a_link_to_a_file_that_does_not_ship_blocks_with_its_fix(source):
    link_from_ledger_knowledge(
        source,
        "[books](../../../CONNECTORS.md)\n\n"
        "[audit](../../ledger-audit/SKILL.md)\n\n"
        "[gone](../../nowhere.md)\n\n"  # dead in the source: the plugin checks' job
        "[web](https://example.invalid/x.md) [anchor](#top) [<skill-dir>](../<x>/y.md)\n",
    )
    res = resolved(source, INCLUDE)
    assert [(d.at.rsplit("/", 1)[1], d.target, d.fix) for d in res.dead_links] == [
        ("links.md:1", "../../../CONNECTORS.md", "plugin-extras: {mcp: true}"),
        ("links.md:3", "../../ledger-audit/SKILL.md", "include: [ledger:ledger-audit]"),
    ]


def test_a_link_into_a_skill_that_does_not_ship_suggests_including_it(source):
    (source / "plugins/billing/skills/invoice-export/notes.md").write_text(
        "[audit](../../../ledger/skills/ledger-audit/SKILL.md)\n"
    )
    git(source, "add", "-A")
    res = resolved(source, INCLUDE, "{shape: marketplace}")
    assert [(d.target, d.fix) for d in res.dead_links] == [
        (
            "../../../ledger/skills/ledger-audit/SKILL.md",
            "include: [ledger:ledger-audit]",
        )
    ]


def test_a_link_resolves_when_its_target_ships(source):
    link_from_ledger_knowledge(source, "[books](../../../CONNECTORS.md)\n")
    assert resolved(source, INCLUDE + "plugin-extras: {mcp: true}\n").dead_links == []


def test_links_in_templates_are_not_checked(source):
    (source / "plugins/billing/skills/invoice-export/templates/x.md").write_text(
        "[books](../../../../ledger/CONNECTORS.md)\n"
    )
    git(source, "add", "-A")
    assert resolved(source, INCLUDE).dead_links == []


def test_a_dead_link_fails_the_lock(source, capsys):
    link_from_ledger_knowledge(source, "[books](../../../CONNECTORS.md)\n")
    write_profile(source, "acme", INCLUDE + target("{shape: skills}"))
    assert main(["-C", str(source), "lock", "acme"]) == 1
    assert (
        "links.md:1: links `../../../CONNECTORS.md`, which the delivery "
        "does not contain -- plugin-extras: {mcp: true}"
    ) in capsys.readouterr().err


# -- MCP extras --------------------------------------------------------------


def test_mcp_extras_ship_with_their_plugin(source):
    files = build(
        source, INCLUDE + "plugin-extras: {mcp: true}\n", "{shape: marketplace}"
    )
    assert json.loads(files["plugins/ledger/.mcp.json"]) == {
        "mcpServers": {"books": {"command": "books-mcp"}}
    }
    assert "plugins/ledger/CONNECTORS.md" in files
    assert "plugins/ledger/connectors/books.env" in files
    assert not any(n.startswith("plugins/billing/connectors") for n in files)


def test_mcp_extras_land_at_the_root_in_shape_skills(source):
    files = build(source, INCLUDE + "plugin-extras: {mcp: true}\n")
    assert {".mcp.json", "CONNECTORS.md", "connectors/books.env"} <= set(files)


def test_mcp_servers_of_merged_plugins_are_merged(source):
    (source / "plugins/billing/.mcp.json").write_text(
        json.dumps({"mcpServers": {"invoices": {"command": "inv"}}})
    )
    git(source, "add", "-A")
    files = build(
        source,
        INCLUDE + "plugin-extras: {mcp: true}\n",
        "{shape: marketplace, flatten: true, plugin-name: m}",
    )
    assert list(json.loads(files["plugins/m/.mcp.json"])["mcpServers"]) == [
        "books",
        "invoices",
    ]


def test_conflicting_mcp_servers_are_refused(source):
    (source / "plugins/billing/.mcp.json").write_text(
        json.dumps({"mcpServers": {"books": {"command": "other"}}})
    )
    git(source, "add", "-A")
    res = resolved(source, INCLUDE + "plugin-extras: {mcp: true}\n")
    assert any("configure MCP server `books` differently" in e for e in res.errors)


BILLING_CONNECTORS = """# Connectors

## How the skills reach the books

Billing reads the books through the books server; see the books
[CONNECTORS.md](../ledger/CONNECTORS.md).

## Connectors

| Category | Server | Used by |
| --- | --- | --- |
| Books | `books` | `invoice-export` |

## How to connect

### Invoices

Sign in to the invoice portal.
"""

LEDGER_CONNECTORS = """# Connectors

The books server needs a token.

## Connectors

| Category | Server | Used by |
| --- | --- | --- |
| Books | `books` | `ledger-knowledge` |

## How to connect

### Books

Set the token.
"""


def test_connectors_docs_of_flattened_plugins_are_merged(source):
    (source / "plugins/billing/CONNECTORS.md").write_text(BILLING_CONNECTORS)
    (source / "plugins/ledger/CONNECTORS.md").write_text(LEDGER_CONNECTORS)
    git(source, "add", "-A")
    profile_text = INCLUDE + "plugin-extras: {mcp: true}\n"
    assert resolved(source, profile_text).errors == []
    merged = build(source, profile_text)["CONNECTORS.md"]
    # billing depends on ledger, so ledger's text comes first
    assert merged == (
        "# Connectors\n\n"
        "The books server needs a token.\n\n"
        # a section only billing has goes before billing's next section, `## Connectors`
        "## How the skills reach the books\n\n"
        "Billing reads the books through the books server; see the books\n"
        "[CONNECTORS.md](CONNECTORS.md).\n\n"
        "## Connectors\n\n"
        "| Category | Server | Used by |\n"
        "| --- | --- | --- |\n"
        "| Books | `books` | `ledger-knowledge`; `invoice-export` |\n\n"
        "## How to connect\n\n"
        "### Books\n\n"
        "Set the token.\n\n"
        "### Invoices\n\n"
        "Sign in to the invoice portal.\n"
    )


def test_connectors_docs_stay_apart_in_a_marketplace(source):
    (source / "plugins/billing/CONNECTORS.md").write_text(BILLING_CONNECTORS)
    git(source, "add", "-A")
    files = build(
        source,
        INCLUDE + "plugin-extras: {mcp: true}\n",
        "{shape: marketplace}",
    )
    assert files["plugins/billing/CONNECTORS.md"] == BILLING_CONNECTORS
    assert "needs a token" in files["plugins/ledger/CONNECTORS.md"]


def test_other_differing_extras_on_one_path_are_refused(source):
    (source / "plugins/billing/connectors").mkdir()
    (source / "plugins/billing/connectors/books.env").write_text("OTHER=1\n")
    git(source, "add", "-A")
    res = resolved(source, INCLUDE + "plugin-extras: {mcp: true}\n")
    assert any(
        "on the same path `connectors/books.env` -- use shape `marketplace`" in e
        for e in res.errors
    )


@pytest.mark.parametrize("name", ["..", "a/b", "a\\b", "."])
def test_a_lock_naming_a_plugin_outside_plugins_is_refused(name):
    from skillship.layout import Layout

    with pytest.raises(SkillshipError, match="not a directory name"):
        Layout.from_lock(
            {"layout": {"shape": "marketplace", "flatten": True, "plugin-name": name}}
        )
    with pytest.raises(SkillshipError, match="not a directory name"):
        Layout.from_lock({"layout": {"shape": "plugin", "name": name}})
    with pytest.raises(SkillshipError, match="not a directory name"):
        Layout.from_lock({"layout": {"shape": "marketplace"}}).plugin_dir(name)


@pytest.mark.parametrize("path", ["/etc/x", "a/../../x", ".git/hooks/pre-commit", ""])
def test_a_delivered_path_outside_the_tree_is_refused(path):
    from skillship.layout import check_dest

    with pytest.raises(SkillshipError, match="leaves the delivery tree"):
        check_dest(path)
