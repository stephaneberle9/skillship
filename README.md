# skillship <!-- omit in toc -->

Resolve, lock and deliver curated subsets ("distros") of
[Agent Skills](https://agentskills.io), with their declared dependencies, as
zips, skill uploads or commits to a recipient's skill or plugin repository.

It works on any repository of Agent Skills, including Claude Code plugin
marketplaces, and the skills it delivers run in Claude Code, Claude Cowork and
any other Agent Skills–compatible agent.

> [!NOTE]
> skillship is in early development. `lock` and `build` work for all three
> layouts and the `archive` target. Upload targets, license ids other than
> `inherit` and `none`, `deliver` and `drift` are not implemented yet: a
> profile or command that needs them is refused with exit status 2.

- [What Is This For](#what-is-this-for)
- [Installation](#installation)
- [Usage](#usage)
  - [Declare what each skill requires](#declare-what-each-skill-requires)
  - [Describe a distro](#describe-a-distro)
  - [Lock, build and deliver](#lock-build-and-deliver)
- [Command Reference](#command-reference)
- [Contributing](#contributing)
- [License](#license)

## What Is This For

Typical use cases:

- Hand a customer or partner the skills of one topic, together with every skill
  those skills depend on, and nothing else from your repository.
- Keep that delivery current: a recipient gets a new zip, commit, pull request
  or merge request whenever the selected skills change.
- Review what a recipient receives before it leaves: the resolved selection is
  a committed lock file, so a change to it shows up as a diff.
- Take changes a recipient made to a delivered skill back into your source
  repository as patches.

## Installation

```bash
pip install skillship
# or, with uv:
uv add skillship
```

Requires Python 3.10+.

To run it without installing, for example in CI:

```bash
uvx skillship --version
```

## Usage

skillship reads from a **source repo**, the repository your skills are written
in, and delivers to a **target**: an archive, an upload, or a recipient's
repository. One recipient's selection is a **distro**.

### Declare what each skill requires

A skill names the skills it cannot work without in its `SKILL.md` frontmatter.
Each value is one whitespace-separated string, because the Agent Skills
specification types `metadata` values as strings:

```yaml
metadata:
  requires: >-
    billing-knowledge
    other-plugin:invoice-export
  suggests: >-
    other-plugin:tax-report
```

A bare name is a skill of the same plugin, and `plugin:skill` a skill of
another plugin. skillship follows `requires` when it resolves a distro, and
only reports `suggests`.

### Describe a distro

A distro is a directory `distros/<name>/` in the source repo. Its profile,
`distro.yaml`, states what to deliver and where:

```yaml
recipient: ACME GmbH
include:
  - billing:invoice-*
exclude:
  - billing:invoice-internal-audit
layout:
  shape: skills
targets:
  - type: archive
  - type: git
    url: https://github.com/acme/agent-skills.git
    branch: main
    mode: pull-request
```

The layout shape decides how the delivered tree looks:

| Shape | Delivered tree |
| --- | --- |
| `skills` | `skills/<skill>/`, every skill side by side, without plugins |
| `plugins` | `plugins/<plugin>/` per source plugin that ships a skill, each with a generated `plugin.json` and README, and a `.claude-plugin/marketplace.json` listing them |
| `plugin` | One plugin, `plugins/<name>/`, merging every shipped skill, with its `plugin.json`, README and `marketplace.json` |

A generated `plugin.json` keeps the source plugin's fields except `repository`,
`homepage` and component paths, and its `dependencies` name only plugins that
ship, plus those of other marketplaces. `layout.root` puts the tree in a
subdirectory, `layout.marketplace` names the marketplace (default: the distro
name), and `layout.name` and `layout.description` describe the merged plugin.

`plugin-extras.mcp: true` delivers each shipping plugin's `.mcp.json`,
`CONNECTORS.md` and `connectors/`, merged where the shape merges plugins.
`plugin-extras.readme: true` adds a README listing the skills to the `skills`
shape; the plugin shapes always generate one per plugin.

### Lock, build and deliver

```bash
skillship lock acme        # resolve the profile into distros/acme/distro.lock.yaml
skillship build acme       # write the staging tree and archives to dist/
skillship deliver acme     # run every target of the profile
skillship drift acme       # report what the recipient changed since the last delivery
```

`lock` writes the exact set of skills and files a recipient receives, with the
reason for each, into a lock file you commit. `build` and `deliver` read the
lock and nothing else, and `build` refuses a lock that no longer matches the
profile and the source repo.

The lock lists file paths, not their content: editing the text of a delivered
file leaves it unchanged, while adding, removing or renaming a file or a skill,
or changing what a skill requires, changes it. `skillship lock --all --check`
in CI therefore fails exactly when a commit changes what a recipient receives.

`lock` also reports every mention of a skill that does not ship, in the
delivered Markdown, outside `templates/` and `examples/`. A mention is
`` `/skill` ``, `` `plugin:skill` `` or "the `` `skill` `` skill"; a bare
backticked name counts as an illustration. Each mention gets a status:

| Status | Meaning | Blocks the lock |
| --- | --- | --- |
| `suggested` | A shipped skill lists it under `suggests` | No |
| `conditional` | The paragraph says "when installed" | No |
| `acknowledged` | The profile lists it under `acknowledge:` | No |
| `blocking` | None of the above | Yes |

For a blocking mention, `lock` prints the profile edits that fix it: an
`include:` entry, with the skills it would pull in, or an `acknowledge:` entry.

`build` rewrites the delivered copy, never the source, and the lock counts the
rewrites per file; `skillship lock --explain` prints each one:

- A delivered `SKILL.md` keeps only the `metadata.suggests` entries that ship.
- In the `skills` and `plugin` shapes, plugin-qualified names are flattened,
  because the source plugins no longer exist: `/plugin:skill` becomes `/skill`
  (or `/<name>:skill` in the merged plugin). An attribution after a skill's
  name loses its plugin (or names the merged plugin): "(`` `plugin` ``
  plugin)", "(`` `plugin` `` plugin, when installed)", "(`` `plugin` ``)",
  "…, `` `plugin` `` plugin)" and ", from the `` `plugin` `` plugin when
  installed". Only names of skills that ship are rewritten.

Any other mention of a source plugin in a flattened delivery blocks the lock,
since rewriting it would mean guessing. Reword it in the source, or list the
plugin's name under `acknowledge:` to deliver the mention as it is.

A relative link in delivered Markdown that resolves in the source repo but not
in the delivered tree blocks the lock too, for example a skill linking its
plugin's `CONNECTORS.md` while the MCP extras do not ship. `lock` names the fix:
the `include:` or `plugin-extras` entry that ships the target, or a rewording
of the link in the source. Links in `templates/` and `examples/` are not
checked.

## Command Reference

| Command | Description |
| --- | --- |
| `skillship lock <name>` | Resolves a distro's profile into its lock file and reports references that would dangle |
| `skillship lock --all` | Does the same for every distro of the source repo |
| `skillship lock … --check` | Fails when a lock is stale instead of rewriting it; meant for CI |
| `skillship lock … --explain` | Prints every rewrite of the delivered copy as before/after lines |
| `skillship build <name> [--out DIR]` | Builds the staging tree and archives from the lock into `DIR` (default `dist/`) |
| `skillship deliver <name> [--target T] [--dry-run]` | Runs the distro's targets, or one of them; `--dry-run` writes nothing |
| `skillship drift <name>` | Reports recipient-side changes to delivered files and writes merge-back patches |
| `skillship -C DIR …` | Uses `DIR` as the source repo instead of the git work tree around the current directory |
| `skillship --version` | Prints the installed version |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

MIT — see [LICENSE](LICENSE).
