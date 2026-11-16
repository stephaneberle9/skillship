"""The `plugins` and `plugin` layouts, the MCP extras, the README, and the rewrites."""

import json
import zipfile

import pytest
from conftest import git, skill_md, write_profile

from skillship import profile
from skillship.cli import main
from skillship.errors import SkillshipError
from skillship.resolve import resolve
from skillship.rewrite import Rewriter
from skillship.source import SourceRepo

INCLUDE = "recipient: ACME\ninclude: [billing:invoice-export]\nlicense: none\n"


def build(source, text):
    write_profile(source, "acme", text + "targets: [{type: archive}]\n")
    assert main(["-C", str(source), "lock", "acme"]) == 0
    assert main(["-C", str(source), "build", "acme"]) == 0
    zf = zipfile.ZipFile(source / "dist/acme/acme.zip")
    return {n: zf.read(n).decode() for n in zf.namelist()}


def resolved(source, text):
    write_profile(source, "acme", text)
    repo = SourceRepo(source)
    return resolve(repo, profile.load(source, "acme"))


# -- flattening --------------------------------------------------------------

SKILLS_OF = {"ledger": {"ledger-knowledge", "ledger-audit"}, "billing": {"invoice"}}
SHIPPED = {"ledger:ledger-knowledge", "billing:invoice"}
FLAT = Rewriter(SKILLS_OF, SHIPPED, "skills")


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


def test_flattening_for_shape_plugin_names_the_merged_plugin():
    flat = Rewriter(
        {"ledger": {"ledger-knowledge"}}, {"ledger:ledger-knowledge"}, "plugin", "acme"
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
    ("shape", "before", "after"),
    [
        # unshipped entries go; a shipped one stays, flattened where the shape flattens
        (
            "skills",
            "metadata:\n  suggests: >-\n    ledger:ledger-audit\n    ledger:ledger-knowledge\n",
            "metadata:\n  suggests: >-\n    ledger-knowledge\n",
        ),
        (
            "plugins",
            "metadata:\n  suggests: >-\n    ledger:ledger-audit\n    ledger:ledger-knowledge\n",
            "metadata:\n  suggests: >-\n    ledger:ledger-knowledge\n",
        ),
        # nothing left: the key goes, and `metadata` with it when it held nothing else
        ("plugins", "metadata:\n  suggests: ledger:ledger-audit\n", ""),
        (
            "plugins",
            "metadata:\n  requires: invoice\n  suggests: ledger:ledger-audit\n",
            "metadata:\n  requires: invoice\n",
        ),
        (
            "plugins",
            "metadata:\n  suggests: ledger:ledger-audit\n  other: x\n",
            "metadata:\n  other: x\n",
        ),
        # a bare entry is a skill of the skill's own plugin
        (
            "plugins",
            "metadata:\n  suggests: invoice gone\n",
            "metadata:\n  suggests: invoice\n",
        ),
    ],
)
def test_suggests_keeps_only_what_ships(shape, before, after):
    skills_of = {**SKILLS_OF, "billing": {"invoice", "gone"}}
    out = Rewriter(skills_of, SHIPPED, shape).rewrite(
        front(before), "billing:invoice", plugin="billing"
    )
    assert out.text == front(after)


def test_suggests_is_not_pruned_outside_a_skill_md():
    text = front("metadata:\n  suggests: ledger:ledger-audit\n")
    assert Rewriter(SKILLS_OF, SHIPPED, "plugins").rewrite(text).text == text


def test_other_mentions_of_a_source_plugin_are_unflattened():
    out = FLAT.rewrite(
        "Intro.\n\nSee the `ledger` plugin's README.\n\n(`ledger` plugin) alone.\n"
    )
    assert out.unflattened == [("ledger", 3), ("ledger", 5)]
    assert out.count == 0


# -- shape plugins -----------------------------------------------------------


def test_shape_plugins_delivers_a_marketplace_with_trimmed_manifests(source):
    files = build(
        source, INCLUDE + "layout: {shape: plugins, marketplace: acme-market}\n"
    )
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


def test_shape_plugins_readme_lists_the_shipped_skills_only(source):
    readme = build(source, INCLUDE + "layout: {shape: plugins}\n")[
        "plugins/billing/README.md"
    ]
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
    readme = build(source, INCLUDE + "layout: {shape: plugins}\n")[
        "plugins/ledger/README.md"
    ]
    background = readme.split("## Background Skills")[1]
    assert "[`ledger-rules`](skills/ledger-rules/SKILL.md)" in background
    assert "/ledger-knowledge" in readme.split("## Background Skills")[0]


def test_inherit_keeps_the_manifest_license(source):
    files = build(
        source, INCLUDE.replace("license: none\n", "") + "layout: {shape: plugins}\n"
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
    res = resolved(source, INCLUDE + "layout: {shape: plugins}\n")
    assert [(p.name, p.dependencies) for p in res.plugins] == [
        ("billing", ["ledger"]),
        ("ledger", []),
    ]


def test_shape_plugins_needs_a_plugin_for_root_skills(tmp_path):
    (tmp_path / "skills/solo").mkdir(parents=True)
    (tmp_path / "skills/solo/SKILL.md").write_text(skill_md("solo"))
    res = resolved(
        tmp_path, "recipient: A\ninclude: [solo]\nlayout: {shape: plugins}\n"
    )
    assert any(
        "root skills have no `.claude-plugin/plugin.json`" in e for e in res.errors
    )
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "solo-plugin"})
    )
    res = resolved(
        tmp_path, "recipient: A\ninclude: [solo]\nlayout: {shape: plugins}\n"
    )
    assert res.errors == []
    assert [p.name for p in res.plugins] == ["solo-plugin"]


def test_readme_false_is_refused_in_a_plugin_shape(source):
    write_profile(
        source,
        "acme",
        INCLUDE + "layout: {shape: plugins}\nplugin-extras: {readme: false}\n",
    )
    with pytest.raises(SkillshipError, match="always generates a README"):
        resolve(SourceRepo(source), profile.load(source, "acme"))


# -- shape plugin ------------------------------------------------------------


def test_shape_plugin_merges_everything_into_one_flattened_plugin(source):
    files = build(
        source,
        INCLUDE
        + "layout: {shape: plugin, name: acme-skills, description: ACME's skills.}\n",
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


def test_shape_plugin_refuses_two_skills_with_one_name(source):
    other = source / "plugins/other/skills/tax-report"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(skill_md("tax-report"))
    res = resolved(
        source,
        "recipient: A\ninclude: [tax-report]\nlayout: {shape: plugin, name: m}\n",
    )
    assert res.errors == [
        (
            "shape `plugin` puts `ledger:tax-report`, `other:tax-report` into the same "
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
    res = resolved(source, INCLUDE + "layout: {shape: plugins}\n")
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
    write_profile(source, "acme", INCLUDE)
    assert main(["-C", str(source), "lock", "acme"]) == 1
    assert (
        "links.md:1: links `../../../CONNECTORS.md`, which the delivery "
        "does not contain -- plugin-extras: {mcp: true}"
    ) in capsys.readouterr().err


# -- MCP extras --------------------------------------------------------------


def test_mcp_extras_ship_with_their_plugin(source):
    files = build(
        source, INCLUDE + "layout: {shape: plugins}\nplugin-extras: {mcp: true}\n"
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
        INCLUDE + "layout: {shape: plugin, name: m}\nplugin-extras: {mcp: true}\n",
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


def test_differing_connectors_docs_are_refused_when_merged(source):
    (source / "plugins/billing/CONNECTORS.md").write_text("# Billing connectors\n")
    git(source, "add", "-A")
    res = resolved(source, INCLUDE + "plugin-extras: {mcp: true}\n")
    assert any("on the same path `CONNECTORS.md`" in e for e in res.errors)
