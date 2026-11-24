---
name: skillship
description: >-
  Drive skillship, the CLI that resolves, locks and delivers curated subsets ("distros") of a
  repository's Agent Skills to a recipient as zips of skills, an uploadable plugin, a
  marketplace, an Agensi pack, or commits to the recipient's repository. Drafts a distro profile
  from a one-sentence request, runs `lock` and walks through the dangling report with the
  profile edit for each entry, guards the locks with a check on every commit, explains a lock diff, picks the shape of each target by how the
  receiver installs it, sets up delivery from
  GitHub Actions to a git target step by step (deploy token, a delivery-only signing key, the
  source repo's secrets, a dry run), goes through recipient drift patch by patch, and submits
  an Agensi pack through Agensi's MCP connector after you confirm the preview. Use when
  creating, changing, checking or delivering a distro, or when a skillship command fails.
---

# skillship

[skillship](https://github.com/stephaneberle9/skillship) reads from a **source
repo**, the repository the skills are written in, and delivers one recipient's
selection, a **distro**, to its **targets**. A distro is `distros/<name>/` in
the source repo: the hand-written profile `distro.yaml` and the generated lock
`distro.lock.yaml`, both committed. The README of the skillship repository is
the reference for every profile key and target; this skill holds the procedures
and the judgment around them.

## Ground rules

- **Run the pinned version.** Read `distros/.skillship-version` in the source
  repo and run `uvx skillship@<version> …`. Without that file, run
  `uvx skillship …` once, note the version `--version` reports, and offer to
  write it there, so CI and every local run agree.
- **Commands run from the source repo**, or with `-C <source-repo>`.
- **Nothing leaves the machine unless the user asks.** `lock`, `build`, `drift`
  and `deliver --dry-run` are local and safe. A real `deliver` with a `git`
  target pushes into a repository the recipient owns, so run it only on an
  explicit request for that distro, and say first what it will do: target,
  branch, mode. The same holds for committing in the source repo, and for an
  Agensi submission.
- **No secret passes through the conversation.** Never ask for a token or key,
  never print one, never read a file that holds one. The user pastes secrets at
  a prompt of their own (see
  [references/ci-delivery.md](references/ci-delivery.md)).
- **Write commands the user runs in their shell's syntax** (PowerShell on
  Windows); the commands you run yourself use your own shell.

## Before the first distro: dependency declarations

skillship follows only what a skill declares under `metadata.requires` in its
frontmatter (README, *Declare what each skill requires*). A repository whose
skills read each other without declaring it resolves to a distro that breaks at
the recipient. Check first:

- If the repository carries plugin checks
  (`scripts/plugin-checks/check-skill-crossrefs.py`), run them. Every `REQUIRE`
  finding must be fixed before a distro is worth locking:
  `check-skill-crossrefs.py --write-requires` adds the hard references (a
  `read:` line, a relative link into another skill, a `<skill-dir>/../<x>/`
  path), and a step handed over in prose ("delegate to X", "run X, then come
  back") is declared by hand.
- Otherwise, grep the skills for relative links and paths into sibling skills,
  and propose the `requires` entries before going further.

## Drafting a distro

From a request like "give ACME the billing skills as a plugin in their GitLab
repo":

1. **Name.** Kebab-case, after the recipient or the purpose (`acme-billing`). It
   becomes the directory, the default marketplace name, and the key of the CI
   secret names (`DISTRO_ACME_BILLING_…`).
2. **Include.** Top-level families only, as `plugin:glob` or a bare glob; the
   closure brings the rest. Show the user which skills match before writing the
   glob.
3. **Targets, each with its shape.** What ships is stated once; every target
   says under `layout.shape` how it lays the delivery out, so one distro can go
   out in several forms. Pick the shape by how the receiver installs it:

   | The receiver | Target |
   | --- | --- |
   | Copies skills into `.claude/skills` (Claude Code, VS Code) | `archive`, shape `skills` (the default) |
   | Uploads a plugin in Claude Desktop or claude.ai | `archive`, shape `plugin` |
   | Adds a marketplace in Claude Code, keeping the source's plugins apart | `archive`, shape `marketplace` |
   | Gets commits into a repository of their own | `git`, shape `skills` or `marketplace`, with `url`, `branch` and `mode` (`push`, `pull-request` on GitHub, `merge-request` on GitLab) |
   | Buys it on Agensi | `agensi` |

   An archive is named `<distro>-<shape>.zip`. When the receivers use both
   Claude Code and Claude Desktop, add both archives to the one distro rather
   than writing a second distro.

   A `plugin` holds every shipped skill in one plugin, named after the distro
   and described by a generated sentence; set `name` and `description` on its
   layout when the defaults read badly. A marketplace keeps the source's
   plugins unless its layout says `flatten: true`, which merges them into one
   plugin (`plugin-name`, `plugin-description`); use that for a `git` target
   whose recipient wants one plugin beside their own.

   `skills`, `plugin` and a flattened marketplace flatten: the source plugins
   do not survive, and their `.mcp.json` and `CONNECTORS.md` files are merged
   into one each. Read the merged `CONNECTORS.md` in
   `dist/<name>/staging/<shape>/` after the first build; where it reads badly,
   the fix is a rewording in the source files, not in the delivery.

   For a customer's repository, default to the reviewed modes: `push` writes
   straight to their branch.
4. **License.** `inherit` ships the source repo's `LICENSE`; `none` leaves the
   content under the recipient's license. Ask; never guess for a paying
   recipient. An SPDX id is part of the profile format but not implemented yet;
   the lock refuses it.
5. **Extras.** `plugin-extras.mcp: true` when a shipped skill drives an MCP
   server of its plugin or links its `CONNECTORS.md`; the lock's dead-link check
   says so if it is missed.

Write `distros/<name>/distro.yaml` with a comment naming the purpose and a
`recipient:` line, then lock it. Always write the line out, also when nobody in
particular is named: a line to edit is easier to get right than one to invent.
For a profile whose targets are all archives, preset it with the text skillship
itself falls back to: "Early adopters, one-off hand-overs by email, Slack,
WhatsApp and the like, for " followed by where the archives are installed.

| Archives of shape | The preset ends with |
| --- | --- |
| `skills` or `marketplace` only | `for Claude Code` |
| `plugin` only | `for Claude Desktop and claude.ai` |
| Both | `for Claude Code, Claude Desktop and claude.ai` |

A `git` or `agensi` target needs a named recipient; ask for it.

## Locking and the dangling report

`skillship lock <name>` writes the lock, or exits non-zero and writes nothing.
Each failure names its fix; walk through them one at a time with the user, never
apply a batch blind:

- **Blocking mention** — a delivered file names a skill that does not ship. The
  report offers `include:` (with the skills that entry would pull in) or
  `acknowledge:`. Recommend:
  - `include:` when the recipient needs that skill for the mentioning one to do
    its job;
  - `acknowledge:` when the mention is a pointer the recipient can live without
    (an optional follow-up, an alternative they will not use);
  - a rewording in the source when the mention is wrong everywhere, for example
    missing the "when installed" a soft reference needs (that makes it
    `conditional`, and fixes every distro at once);
  - `exclude:` to keep a skill out that an `include` glob matches. It cannot
    take out what a shipped skill requires: the lock fails then and names the
    chain, and the requirement has to change in the source first (`requires`
    to `suggests`, where the skill works without it).
- **Unflattened plugin mention** (any flattening layout: `skills`, `plugin`, a
  marketplace with `flatten`) — prose names a
  source plugin in a phrasing flattening does not rewrite. Reword the source
  into an attribution phrasing the README lists, or acknowledge the plugin name
  when the mention is about the plugin as such (a pointer to its
  `CONNECTORS.md`).
- **Dead link** — a relative link that resolves in the source but not in the
  delivered tree. Ship the target (`include:` or `plugin-extras`) or reword the
  link.
- **Two files on one path** (the same layouts) — a flattening
  layout puts the extras of several source plugins into one directory.
  `.mcp.json` and `CONNECTORS.md` files are merged; one MCP server configured
  two ways, or any other two differing files (`connectors/setup.md` in two
  plugins), block the lock. Make them agree in the source, keep the plugins
  apart (shape `marketplace` without `flatten`), or leave the extras out
  with `plugin-extras.mcp: false`.

A problem that only some layouts have names the targets it concerns, as in
`(in acme-plugin.zip)`. Fix it for those; the other targets are unaffected.

- **Upload limits** — a description over 1024 characters, non-string `metadata`,
  or Agensi's file, size or file-type limits. These are fixed in the source.

After it passes, `lock --explain <name>` shows every rewrite of the delivered
copy; skim it with the user on a first lock. A source edit to fix a mention is a
change to the source repo, reviewed and committed like any other.

## Checking the locks on every commit

Once a distro is locked, offer to guard its lock: without a check, a lock goes
stale the first time a shipped skill gains a `requires` entry or a file is
renamed, and nobody notices until a build refuses it.
[templates/check-distro-locks.py](templates/check-distro-locks.py) runs
`lock --all --check` at the pinned version and fails on a stale lock. Copy it
unchanged and wire it in where the repository already runs its checks:

| The repository has | Do this |
| --- | --- |
| The plugin checks (`scripts/plugin-checks/run-plugin-checks.py`) | Copy the script to `scripts/plugin-checks/local/`. The runner calls every `local/check-*.py` before a commit and in CI |
| Git hooks through prek or pre-commit, without the plugin checks | Copy it to `scripts/`, and add the local hook the README shows under *Check the locks on every commit* |
| Neither | Copy it to `scripts/` and add `python scripts/check-distro-locks.py .` as a CI step; say that nothing checks before a commit |

It needs `distros/.skillship-version` (see *Ground rules*) and uv or pipx. Run
it once after wiring it in and show the result. With `SKILLSHIP_FROM` set to a
skillship checkout it runs that working tree instead of the pinned release;
that is for trying an unreleased skillship, never a setting to commit or to
give a CI job.

## Explaining a lock diff

When `lock --check` fails in CI, or a lock changed in a commit, read
`git diff -- distros/<name>/distro.lock.yaml` and say what the recipient gains
or loses, not which YAML lines moved:

| Section | A change means |
| --- | --- |
| `skills[].id` | A skill ships or stops shipping; `reason` says which include or `requires` chain pulled it |
| `skills[].files` | A file of a shipped skill appears, disappears or was renamed |
| `extras` | License and MCP extras |
| `suggested`, `dangling` | Soft references and their sites; informational |
| `layouts[]` | One entry per distinct layout, with the `targets` that use it; a new entry means a target was added or changed shape |
| `layouts[].generated`, `.plugins` | Generated manifests and READMEs, the delivered plugins and their trimmed `dependencies`, in that layout |
| `layouts[].rewritten`, `.unflattened` | Rewrites of the delivered copy in that layout; a count changes when a mention is added or removed |
| `layouts[].uploads` | Keys an Agensi pack drops, and paths an upload cannot reach |
| `target-files` | Files the source repo seeds into a git target, and their ownership class |
| `profile-digest` | The profile changed |

Then recompute with `lock <name>` and let the user decide whether the change is
intended for that recipient. A skill that ships by accident is fixed with
`exclude:` or by narrowing `include:`, or in the source when a shipped skill
requires it, never by editing the lock, which `lock --check` would reject.

## Uploads

Claude Desktop and claude.ai take plugins, not single skills: an archive of
shape `plugin` is the file to upload there. It holds every shipped skill, so
skills that read each other or their shared material keep working. After
locking, read `uploads.unresolved` of that layout in the lock: a path listed
there leads to something that does not ship, and breaks once the plugin is
installed. Ship its target or reword the path in the source.

## Delivering

- **Archives and the Agensi pack:** `skillship build <name>` or
  `deliver <name> --target <type>`; output lands in `dist/<name>/`. Name the
  files and give the user the steps to pass on with a hand-over (from the
  Claude Code docs and the Plugins page of Claude Desktop and claude.ai;
  confirm them on the first real hand-over):

  | File | The receiver |
  | --- | --- |
  | `<distro>-skills.zip` | Copies the folders under `skills/` into `~/.claude/skills/`, or into a project's `.claude/skills/` |
  | `<distro>-plugin.zip` | Uploads it in Claude Desktop or claude.ai under *Plugins › Add › Upload plugin*; for Claude Code, unzips it into `~/.claude/skills/<plugin>/` |
  | `<distro>-marketplace.zip` | Unzips it to a folder that stays, adds that folder as a marketplace (`/plugin marketplace add <folder>`, or the Marketplaces tab of `/plugins` in VS Code) and installs the plugins. Claude Code loads a local marketplace in place, so moving or deleting the folder breaks them; it does not take the zip itself |

  For a one-off hand-over, the first two cover every receiver: the skills zip
  for Claude Code and VS Code, the plugin zip for Claude Desktop and claude.ai.
- **Git targets, locally:** `deliver <name> --dry-run` first: it clones,
  applies, commits and runs the gate in a scratch clone and pushes nothing.
  Report what it would change. Run the real delivery only when asked. `deliver`
  refuses uncommitted changes in the source repo, because the delivery records
  the source commit.
- **Git targets, from CI:** set up once per distro, per
  [references/ci-delivery.md](references/ci-delivery.md): the target repo's
  token, a delivery-only signing key, the source repo's secrets and variables,
  the caller workflow (from
  [templates/deliver-distros.yml](templates/deliver-distros.yml)), and a dry run
  from the Actions tab.

A failed gate stops the delivery before anything is pushed. Read which check
failed: a finding in the delivered skills is fixed in the source; a finding in
the recipient's own files is theirs to fix, so tell the user what to pass on.

## Recipient drift

`deliver` stops when the recipient changed a delivered (`owned`) file. Run
`skillship drift <name>`; it writes one patch per file to `dist/<name>/drift/`
with a verdict. Go through them one at a time: show the patch, say what the
recipient changed and why it might have, and ask whether it belongs upstream.

| Verdict | Take it back | Discard it |
| --- | --- | --- |
| `clean` | `git apply <patch>` in the source repo | `deliver <name> --overwrite <path>` |
| `conflicting` | Apply, resolve the markers with the user | as above |
| `already upstream` | Nothing to do; the next delivery passes | — |
| `deleted` | Remove it from the distro (`files.exclude`, or stop shipping the skill) | `--overwrite` restores it |
| `generated`, `no base` | No source to patch; carry the intent into the source by hand | `--overwrite` |

A change worth keeping for every recipient goes into the source; one that only
fits this recipient means the distro should stop shipping that file, or the
recipient keeps a fork of it. Leave the applied patches uncommitted until the
user asks to commit, then deliver.

## Agensi

An `agensi` target builds `dist/<name>/agensi/<name>.zip` and
`<name>.preflight.md`. Submitting it goes through Agensi's MCP connector in an
interactive session; see
[references/agensi-submission.md](references/agensi-submission.md).

## When a command fails

- **Exit 2, "is not implemented yet"**: the profile uses a value or target this
  skillship version lacks. A schema error names the key; compare with the README
  of the pinned version, or raise the pin.
- **`build` refuses the lock**: it is stale; run `lock <name>` and review the
  diff.
- **"`layout` now belongs to a target"**, or **"the lock has no layout of …
  for this target"**: the profile or lock was written for skillship before
  0.8.0. Move the top-level `layout` under each `archive` or `git` target;
  `merged-plugin: {name, description}` becomes `flatten: true` with
  `plugin-name` and `plugin-description`; a `plugin-upload` target becomes an
  `archive` with `layout: {shape: plugin}`; drop a `skill-upload` target and
  `upload.inline-shared`. Then lock again. Archives are now named
  `<distro>-<shape>.zip`.
- **"the target holds the delivery of distro …"**: one target repo receives one
  distro.
- **"cannot clone"**: the branch is missing or the credentials do not reach the
  repository. `deliver` never makes a repository's first commit, so the
  recipient creates the branch with one commit first.
