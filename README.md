# skillship <!-- omit in toc -->

Resolve, lock and deliver curated subsets ("distros") of
[Agent Skills](https://agentskills.io), with their declared dependencies, as
zips, skill uploads or commits to a recipient's skill or plugin repository.

It works on any repository of Agent Skills, including Claude Code plugin
marketplaces, and the skills it delivers run in Claude Code, Claude Cowork and
any other Agent Skills–compatible agent.

> [!NOTE]
> skillship is in early development. The four commands exist and parse their
> arguments, but none of them does its work yet: each prints
> `not implemented yet` and exits with status 2. The `skillship` package on
> PyPI is a placeholder that reserves the name.

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

### Lock, build and deliver

```bash
skillship lock acme        # resolve the profile into distros/acme/distro.lock.yaml
skillship build acme       # write the staging tree and archives to dist/
skillship deliver acme     # run every target of the profile
skillship drift acme       # report what the recipient changed since the last delivery
```

`lock` writes the exact set of skills and files a recipient receives, with the
reason for each, into a lock file you commit. `build` and `deliver` read the
lock and nothing else.

## Command Reference

| Command | Description |
| --- | --- |
| `skillship lock <name>` | Resolves a distro's profile into its lock file and reports references that would dangle |
| `skillship lock --all` | Does the same for every distro of the source repo |
| `skillship lock … --check` | Fails when a lock is stale instead of rewriting it; meant for CI |
| `skillship lock … --explain` | Prints every rewrite of a plugin-qualified skill name as a before/after line |
| `skillship build <name> [--out DIR]` | Builds the staging tree and archives from the lock into `DIR` (default `dist/`) |
| `skillship deliver <name> [--target T] [--dry-run]` | Runs the distro's targets, or one of them; `--dry-run` writes nothing |
| `skillship drift <name>` | Reports recipient-side changes to delivered files and writes merge-back patches |
| `skillship --version` | Prints the installed version |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

MIT — see [LICENSE](LICENSE).
