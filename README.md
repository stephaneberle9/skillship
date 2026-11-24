# skillship <!-- omit in toc -->

Resolve, lock and deliver curated subsets ("distros") of
[Agent Skills](https://agentskills.io), with their declared dependencies, as
zips of skills, uploadable plugins, marketplaces or commits to a recipient's
skill or plugin repository.

It works on any repository of Agent Skills, including Claude Code plugin
marketplaces, and the skills it delivers run in Claude Code, Claude Cowork and
any other Agent Skills–compatible agent.

> [!NOTE]
> skillship is in early development. License ids other than `inherit` and
> `none` are not implemented yet: a profile that needs one is refused with exit
> status 2.

- [What Is This For](#what-is-this-for)
- [Installation](#installation)
- [Usage](#usage)
  - [Declare what each skill requires](#declare-what-each-skill-requires)
  - [Describe a distro](#describe-a-distro)
  - [Lock, build and deliver](#lock-build-and-deliver)
  - [Check the locks on every commit](#check-the-locks-on-every-commit)
  - [Uploads](#uploads)
  - [Deliver into a git repository](#deliver-into-a-git-repository)
  - [Take recipient changes back](#take-recipient-changes-back)
  - [Deliver from CI](#deliver-from-ci)
- [Claude Code Plugin](#claude-code-plugin)
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
in, and delivers to a **target**: an archive, an Agensi pack, or a recipient's
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
targets:
  - type: archive
    layout:
      shape: skills
  - type: archive
    layout:
      shape: plugin
  - type: git
    url: https://github.com/acme/agent-skills.git
    branch: main
    mode: pull-request
    layout:
      shape: marketplace
```

`include` selects skills, and everything they require comes along. `exclude`
names skills that must never reach this recipient: it takes them out of what
`include` matches, as in a glob with one exception. It cannot take out what a
shipped skill requires; the lock fails then, and names the chain.

What ships is stated once, for the whole distro. How it is laid out is stated
per target, so one distro can go out in several forms at once: here as a zip of
skills, as a plugin to upload, and as a marketplace in ACME's repository.

`recipient` says who receives the distro, for people reading the profile. A
profile may leave it out when nobody in particular is named and its targets
are all archives. The recipient then reads as "Early adopters, one-off
hand-overs by email, Slack, WhatsApp and the like", followed by where the
archives are installed:

| Archive of shape | The default recipient ends with |
| --- | --- |
| `skills`, `marketplace` | for Claude Code |
| `plugin` | for Claude Desktop and claude.ai |

A profile with both kinds of archive ends with "for Claude Code, Claude Desktop
and claude.ai". A `git` or `agensi` target needs a named recipient.

A target's `layout.shape` decides how its delivered tree looks. An `archive`
target zips that tree as `<distro>-<shape>.zip`:

| Shape | Delivered tree | What the receiver does with the zip |
| --- | --- | --- |
| `skills` (the default) | `skills/<skill>/`, every skill side by side, without plugins | Copies the skill folders into `.claude/skills` |
| `plugin` | One plugin holding every shipped skill: `.claude-plugin/plugin.json`, a README and `skills/<skill>/` | Uploads it as a plugin in Claude Desktop or claude.ai |
| `marketplace` | A marketplace: `plugins/<plugin>/` per source plugin that ships a skill, one or several, each with a generated `plugin.json` and README, and a `.claude-plugin/marketplace.json` listing them | Unpacks it and adds the directory with `/plugin marketplace add <directory>` in Claude Code |

Two archives of the same shape would write the same file; give one of them a
`name`, which replaces the whole file name.

A `plugin` is named after the distro and described by a sentence naming the
included skills. `name` and `description` on its layout say otherwise:

```yaml
layout:
  shape: plugin
  name: acme-billing
  description: Billing skills for ACME.
```

A marketplace keeps the source's plugins. With `flatten: true` it holds one
plugin instead: every shipped skill is merged into `plugins/<name>/`, whichever
source plugin it comes from. That plugin is named and described like a
`plugin`, with `plugin-name` and `plugin-description`:

```yaml
layout:
  shape: marketplace
  flatten: true
  plugin-name: acme-billing
  plugin-description: Billing skills for ACME.
```

A generated `plugin.json` keeps the source plugin's fields except `repository`,
`homepage` and component paths, and its `dependencies` name only plugins that
ship, plus those of other marketplaces. `layout.root` puts the tree in a
subdirectory, and `layout.marketplace` names the marketplace (default: the
distro name). A `git` target takes the shapes `skills` and `marketplace`.

`plugin-extras.mcp: true` delivers each shipping plugin's `.mcp.json`,
`CONNECTORS.md` and `connectors/`. `plugin-extras.readme: true` adds a README
listing the skills to the `skills` shape; a plugin always gets one.

The `skills` and `plugin` shapes and a marketplace with `flatten` **flatten**: the
source plugins do not survive, so the extras of several source plugins land on
one path. There they are merged:

- `.mcp.json`: the servers side by side; one server name with two different
  configurations blocks the lock.
- `CONNECTORS.md`: one document. Sections with the same `##` heading become
  one section, and a section only one file has keeps its place ahead of the
  sections that follow it there. A paragraph, list or alert that an earlier
  file already has is dropped, and tables with the same header row become one
  table, where rows that differ only in their last cell become one row with
  both cells. The text of a plugin follows that of the plugins it depends on,
  and a link from one of the merged files to another points at the merged
  file. A `###` heading that two files use for different text appears twice;
  reword one in the source.
- Any other file, such as two different `connectors/setup.md`, blocks the lock.

### Lock, build and deliver

```bash
skillship lock acme        # resolve the profile into distros/acme/distro.lock.yaml
skillship build acme       # write the staging trees and archives to dist/
skillship deliver acme     # run every target of the profile
skillship drift acme       # report what the recipient changed since the last delivery
```

`lock` writes the exact set of skills and files a recipient receives, with the
reason for each, into a lock file you commit. `build` and `deliver` read the
lock and nothing else, and `build` refuses a lock that no longer matches the
profile and the source repo.

The lock lists the skills and files once. What depends on a target's layout,
such as the delivered plugins, the generated files and the rewrites, follows
under `layouts`, once per distinct layout with the targets that use it.

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
- In a flattening layout, plugin-qualified names are flattened,
  because the source plugins no longer exist: `/plugin:skill` becomes `/skill`
  (or `/<name>:skill` in the merged plugin). An attribution after a skill's
  name loses its plugin (or names the merged plugin): "(`` `plugin` ``
  plugin)", "(`` `plugin` `` plugin, when installed)", "(`` `plugin` ``)",
  "…, `` `plugin` `` plugin)" and ", from the `` `plugin` `` plugin when
  installed". Only names of skills that ship are rewritten.
- In the `skills` shape, where the delivery has no plugin at all, a pointer to
  a file of the skill's own plugin loses the plugin: "this plugin's
  `` `CONNECTORS.md` ``" becomes "the `` `CONNECTORS.md` ``".

Any other mention of a source plugin in a flattened delivery blocks the lock,
since rewriting it would mean guessing. Reword it in the source, or list the
plugin's name under `acknowledge:` to deliver the mention as it is.

A relative link in delivered Markdown that resolves in the source repo but not
in the delivered tree blocks the lock too, for example a skill linking its
plugin's `CONNECTORS.md` while the MCP extras do not ship. `lock` names the fix:
the `include:` or `plugin-extras` entry that ships the target, or a rewording
of the link in the source. Links in `templates/` and `examples/` are not
checked.

### Check the locks on every commit

A lock goes stale without anyone touching a distro: a skill gains a
`metadata.requires` entry, a new skill matches an `include:` glob, a shipped
file is renamed. `skillship lock --all --check` catches that, and it belongs
where every commit passes: a git hook and CI.

The Claude Code plugin of this repository ships a script for it,
[`check-distro-locks.py`](plugins/skillship/skills/skillship/templates/check-distro-locks.py).
It needs Python and [uv](https://docs.astral.sh/uv/) or pipx, runs the version
pinned in `distros/.skillship-version`, takes the repository root as its
argument, and exits non-zero on a stale lock. Copy it into the source repo and
run it as a hook, here with [prek](https://prek.j178.dev/) or pre-commit:

```yaml
- repo: local
  hooks:
    - id: distro-locks
      name: distro locks
      entry: python scripts/check-distro-locks.py .
      language: python
      pass_filenames: false
      always_run: true
```

In CI, run the same hooks, or the script as a step of its own. In GitHub
Actions its report also goes into the job summary.

To check with a skillship that is not released yet, set `SKILLSHIP_FROM` to its
checkout. The script then runs that working tree instead of the pinned release
and says so.

### Uploads

Two things a distro builds are uploaded somewhere rather than copied or added:

| Target | Writes | Upload it at |
| --- | --- | --- |
| `archive` of shape `plugin` | `<distro>-plugin.zip`, one plugin with `.claude-plugin/plugin.json` at the top | Claude Desktop or claude.ai: *Plugins › Add › Upload plugin* |
| `agensi` | One Agensi skill pack, `agensi/<name>.zip`, and its pre-flight report, built from the `skills` layout | [Agensi](https://www.agensi.io), a marketplace for paid skills, through its MCP connector |

A plugin keeps the frontmatter Claude Code reads. For either upload, a
description longer than 1024 characters, or `metadata` that does not map keys
to strings, blocks the lock.

An upload is installed on its own, so a path that leaves it no longer
resolves. The lock lists each such path that resolves in the source under
`uploads.unresolved` of the layout concerned. A plugin holds every shipped
skill, so it suits a distro whose skills read each other: only a path to
something that does not ship is listed.

An `agensi` package is a skill pack: a `README.md` whose heading names the
pack (`title:` on the target, by default the distro name), a `skills/<name>/`
folder per skill, and the shipped `shared-material/` as `shared/`, with the
paths into it rewritten to `../../shared/…`. A distro of one skill becomes a
single skill instead. A pack skill sees its own folder and `shared/` only, so
paths to other skills are listed under `uploads.unresolved`. Its frontmatter
keeps `name`, `description`, `license`, `compatibility`, `metadata` and
`allowed-tools` plus the listing fields Agensi reads (`summary`, `version`, `tags`, `keywords`, `use_cases`,
`docs_url`). The lock enforces Agensi's limits for an upload from an agent:
at most 100 files, 5 MB and 50 skills, text only, no executables and no bundled
dependencies.

`build` also writes `<name>.preflight.md`, a heuristic of Agensi's security
scan: its eight checks (file structure, file types, dangerous commands,
secrets, environment harvesting, network access, obfuscation, prompt
injection), each pass, warn or fail, and the score Agensi computes, 100 minus
20 per failed check and 5 per warning. It notes scripts that read files next
to them, which Agensi runs alone, and scripts over 20 KB. Submitting the
package is up to you or your agent: Agensi's sign-in works interactively only.

### Deliver into a git repository

A `git` target delivers into a repository the recipient owns, as one signed
commit (`git commit -S`, so your git configuration picks SSH or GPG):

```yaml
targets:
  - type: git
    url: https://github.com/acme/agent-skills.git
    branch: main          # must exist; default: the repository's default branch
    mode: pull-request    # push (default) | pull-request (GitHub) | merge-request (GitLab)
    forge: github         # only when the URL's host does not say it
```

`deliver` clones the target branch, applies the delivery, commits, runs the
gates (below) and only then pushes, or pushes the branch `skillship/<distro>`
and opens a pull request with `gh` or a merge request with `glab`. It
authenticates through your git credential helper and the `gh` or `glab` login.
`--dry-run` does all of it in a scratch clone and pushes nothing. `deliver`
refuses a source repo with uncommitted changes, because the delivery records
the source commit it was made from.

`.skillship.json`, at the root of the target repo, records the delivery: the
distro, the source commit, the lock digest, and for every file its
**ownership class** and a SHA-256. The class decides what the next delivery
does with the file:

| Class | Files | On the next delivery |
| --- | --- | --- |
| `owned` | Delivered skills, generated `plugin.json` and README, owned target files | Replaced, and removed when they stop shipping |
| `seeded` | Seeded target files, a `.gitlab-ci.yml` skillship created | Created when missing, never overwritten; a newer source version is announced to the recipient |
| `announced` | A seeded target file the recipient already had | Never written; the `announce` text is shown when it changes |
| `merged` | `.claude-plugin/marketplace.json` | Only the delivered plugins' entries change; the recipient's other plugins stay listed |

What the last delivery wrote is taken from the source repo, not from the
record: `deliver` rebuilds that delivery from the source commit the record
names, and removes only its files that no longer ship, and drops only its
plugins' marketplace entries. Anyone who can push to the target can edit the
record, and an entry added there never makes a delivery delete a file. When
the commit cannot be rebuilt, nothing is removed, and `deliver` says so.

A `seeded` file is the recipient's once created, so `deliver` never looks at
it again, edited or not. When its template in the source repo changes, the next
delivery announces it: the delivery commit's message, and the description of
the pull or merge request, carry a patch from the version originally seeded to
the new one. The recipient applies it with `git apply -3`, a three-way merge
against that seeded version, which their history always holds: it keeps their
edits, including changes taken from an earlier patch, and leaves conflict
markers only where they and the template changed the same or adjacent lines.
In `push` mode, a template change alone makes a delivery commit, so the
announcement always reaches the target repo. The record keeps the commit a file
was seeded from (`seeded-from`) and the version announced last
(`source-sha256`), so each change is announced once.

Every other file of the target repo is never written. When the recipient
versions a delivered plugin (`version` in its `plugin.json`), `deliver` keeps
that version and raises its patch number whenever the plugin's files change;
`version: keep` in the profile keeps it unchanged.

**Target files.** A source repo can give every `git` target of a marketplace
files of its own, such as the CI checks a plugin repository runs. They are
listed in `distros/skillship.yaml`, shared by all distros, in named sets:

```yaml
target-files:
  plugin-checks:
    gate: python templates/plugin-checks/run-plugin-checks.py --root {target} --shared-only --base {base}
    files:
      - from: templates/plugin-checks/      # a directory: its git-tracked files
        to: scripts/plugin-checks/
        ownership: owned
        rename: {readme.md: README.md}
      - from: templates/github/run-static-checks.yml
        to: .github/workflows/run-static-checks.yml
        when: {forge: github}
        ownership: seeded
      - from:                               # files joined in order
          - templates/pre-commit-config.yaml
          - templates/pre-commit-config.plugin-checks.yaml
        to: .pre-commit-config.yaml
        announce: templates/pre-commit-config.plugin-checks.yaml
        ownership: seeded
```

A `seeded` file can be joined from several files of the source repo, listed in
`from`, and can name an `announce` file. When the target repo already has a
file of its own at that path, skillship leaves it alone and shows the
recipient the announced text instead, in the delivery commit's message and the
pull or merge request, under *Files you already had*: here, the hook to add to
the recipient's own `.pre-commit-config.yaml`. The record marks such a file
`announced` and keeps the text's hash, so the text is shown again only when it
changes. Neither works for an `owned` file.

The lock lists each target file with its ownership class. `gate` runs after
the commit and before the push, split like a POSIX shell command line, with
`{target}` replaced by the path of the target clone and `{base}` by
`origin/<branch>`; a failure stops the delivery. It runs in the source repo,
never in the clone, and should read the clone only as data: code the recipient
wrote, such as checks of their own, would otherwise run on the sender's machine
with the sender's credentials and signing key. Those checks run in the
recipient's own CI. On GitLab, a delivered `.gitlab/ci/*.yml` job file needs an
`include:` in `.gitlab-ci.yml`: skillship creates that file when the target has
none, and otherwise prints the lines to add. A profile opts out of a set with
`target-files: {plugin-checks: false}`.

### Take recipient changes back

An `owned` file the recipient changed or deleted since the last delivery is
**drift**, and `deliver` stops on it. `skillship drift acme` writes, for each
changed file, a patch against the source repo into `dist/acme/drift/`, ready
for `git apply`. The patch carries the recipient's change from the delivered
copy back onto the source file as it was delivered, so a rewrite of the
delivered copy, such as a flattened plugin name, never shows up as a change,
and from there onto the source as it is today. Each file gets a verdict:

| Verdict | Meaning |
| --- | --- |
| `clean` | The patch applies to the source as it is |
| `conflicting` | The patch holds conflict markers to resolve |
| `already upstream` | The source already holds the change |
| `deleted` | The recipient deleted the file |
| `generated` | skillship writes the file itself, so there is no source to patch |
| `no base` | The delivered version cannot be rebuilt from the recorded source commit |

Once the source holds the recipient's change, the next delivery writes what
the recipient already has, and `deliver` no longer stops on that file; the
same goes for a deleted file that no longer ships. To discard a recipient's
change instead, deliver with `--overwrite PATH`.

### Deliver from CI

The reusable workflow `.github/workflows/distros.yml` delivers from GitHub
Actions. Call it from a workflow of the source repo's own, such as
`deliver-distros.yml`, pinned to the commit of a skillship
release tag (it receives the source repo's secrets, and a tag can be moved), and
pin the same CLI version in `distros/.skillship-version`. The
[skillship plugin](#claude-code-plugin) ships the caller as a template
(`plugins/skillship/skills/skillship/templates/deliver-distros.yml`); in short:

```yaml
# .github/workflows/deliver-distros.yml
name: Deliver distros
on:
  workflow_dispatch:
    inputs:
      distro: { type: string, default: "" }
      dry-run: { type: boolean, default: true }
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  deliver:
    # The commit of the release tag: gh api repos/stephaneberle9/skillship/commits/v<version> --jq .sha
    uses: stephaneberle9/skillship/.github/workflows/distros.yml@<sha> # v<version>
    with:
      distro: ${{ inputs.distro || '' }}
      dry-run: ${{ inputs.dry-run || false }}
      auto-only: ${{ github.event_name == 'push' }}
    secrets: inherit
```

A run from the Actions tab delivers one distro or all; a push to `main`
delivers the distros whose profile says `auto: true`. Each distro gets its own
job. Archives and Agensi packs become a workflow artifact, and the output goes
into the job summary.

A `git` target needs credentials, which no CI runner has. `deliver` and
`drift` read them from environment variables, and the workflow fills those
from secrets and variables named per distro, with `KEY` the distro's name
upper-cased and `_` for `-` (`acme-billing` → `ACME_BILLING`):

| Name | Kind | Holds |
| --- | --- | --- |
| `DISTRO_<KEY>_DEPLOY_TOKEN` | secret | A token with write access to the target repo: on GitHub a fine-grained personal access token or a GitHub App token, on GitLab a project access token (or a deploy token for `push` mode). Sent to git as an `Authorization` header for the target's host only, and to `gh` or `glab` |
| `DISTRO_<KEY>_DEPLOY_USER` | variable | The token's user name, if it needs one: a GitLab deploy token's. Defaults to `x-access-token` on GitHub, `oauth2` on GitLab |
| `DISTRO_<KEY>_SIGNING_KEY` | secret | An OpenSSH or an armored GPG private key, which signs the delivery commit |
| `DISTRO_<KEY>_SIGNING_KEY_PASSPHRASE` | secret | The key's passphrase, if it has one |
| `DISTRO_<KEY>_COMMITTER_NAME`, `DISTRO_<KEY>_COMMITTER_EMAIL` | variable | Who the commit is by |

Each falls back to the same name without `<KEY>_`, for settings all distros
share. The forge shows the commit as verified when the key's public half is
registered as a signing key with an account that holds the committer's
email; a `users.noreply` address of that account works. Use a key made for
deliveries, never the one that signs your own commits: a CI secret is exposed to
every workflow run of the source repo, and revoking a leaked delivery key then
leaves your own signing intact. One delivery key per source repo, shared by its
targets, is a reasonable default; it is registered once per forge account, not
per target repo. With the input
`environments: true`, each job runs in the GitHub environment
`distro-<name>`, whose secrets serve as the unprefixed names and whose
required reviewers gate the delivery. Environments need a public repository,
or a paid plan for a private one.

The token is never written to a file or a command line, and a merge request
goes through GitLab's REST API, so CI needs no `glab`. The gate runs without
these variables in its environment; it is the source repo's own code, though,
trusted like the workflow that runs it.

## Claude Code Plugin

This repository is also a Claude Code plugin marketplace. Its `skillship`
plugin lets Claude drive the CLI: draft a distro from a sentence, walk through
the dangling report of `lock`, explain a lock diff, choose each target's shape,
check the locks on every commit, set up delivery from CI step by step, go
through recipient drift, and submit an Agensi pack.

```text
/plugin marketplace add stephaneberle9/skillship
/plugin install skillship@skillship
```

See [plugins/skillship/README.md](plugins/skillship/README.md).

## Command Reference

| Command | Description |
| --- | --- |
| `skillship lock <name>` | Resolves a distro's profile into its lock file and reports references that would dangle |
| `skillship lock --all` | Does the same for every distro of the source repo |
| `skillship lock … --check` | Fails when a lock is stale instead of rewriting it; meant for CI |
| `skillship lock … --explain` | Prints every rewrite of the delivered copy as before/after lines |
| `skillship build <name> [--out DIR]` | Builds a staging tree per layout, the archives and the Agensi pack from the lock into `DIR` (default `dist/`) |
| `skillship deliver <name> [--target TYPE] [--dry-run]` | Runs the distro's targets, or those of one type; `--dry-run` pushes nothing and builds no archive |
| `skillship deliver … --overwrite PATH` | Replaces a delivered file the recipient changed; repeatable |
| `skillship drift <name> [--out DIR]` | Reports recipient-side changes to delivered files and writes merge-back patches to `DIR/<name>/drift/` |
| `skillship list [--auto] [--json]` | Lists the distros, or those marked `auto: true`; `--json` adds each one's secret-name key and target types |
| `skillship -C DIR …` | Uses `DIR` as the source repo instead of the git work tree around the current directory |
| `skillship --version` | Prints the installed version |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

MIT — see [LICENSE](LICENSE).
