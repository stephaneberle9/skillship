# Plugin checks

Automated checks for this plugin repository. They run as the `plugin-checks` hook of
`.pre-commit-config.yaml`, with [prek](https://prek.j178.dev/): in CI on every pull or merge
request and on every push to the default branch (the static-checks job), and before every
local commit once the hook is active. Activate it once per clone with:

```sh
prek install -f
```

`-f` replaces a hook that an earlier `pre-commit install` left behind.

## Without prek: a `.githooks/pre-commit` script

A repository whose owner keeps hook scripts of their own in `.githooks/` instead runs
neither prek's hook nor its CI job: `core.hooksPath` makes git ignore what `prek install`
writes into `.git/hooks/`. There the script must call the runner itself, and a CI job of
the repository's own must run it too, as `python scripts/plugin-checks/run-plugin-checks.py`
with PyYAML installed. The `claude-plugin-knowledge` skill ships such a script
(`templates/githooks/pre-commit`); its core is:

```sh
#!/bin/sh
root=$(git rev-parse --show-toplevel) || exit 1
runner="$root/scripts/plugin-checks/run-plugin-checks.py"
if command -v uv >/dev/null 2>&1; then
  exec uv run --quiet --no-project --with pyyaml python "$runner"
fi
exec python3 "$runner"   # needs PyYAML installed
```

Commit it executable and activate it once per clone:

```sh
git add --chmod=+x .githooks/pre-commit
git config core.hooksPath .githooks
```

A script that already runs other checks calls the runner as one more line and keeps its
exit status, for example `uv run --quiet --no-project --with pyyaml python
scripts/plugin-checks/run-plugin-checks.py || exit 1`. If the repository also has a
`.pre-commit-config.yaml`, end the script with `prek run` and leave the `plugin-checks`
hook out of that file, so the checks do not run twice.

## By hand

Run them by hand from the repository root (PyYAML is the only dependency):

```sh
uv run --no-project --with pyyaml python scripts/plugin-checks/run-plugin-checks.py
uv run --no-project --with pyyaml python scripts/plugin-checks/run-plugin-checks.py --base origin/main
```

`--base` adds the version policy against the target branch. In CI the prek hook of
`.pre-commit-config.yaml` runs the same script, and in a pull or merge request pipeline
it finds and fetches the target branch itself (`GITHUB_BASE_REF`,
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME`).

## What they check

| Check | Finds |
| --- | --- |
| `check-skill-frontmatter.py` | Frontmatter that Claude Code drops silently or the claude.ai marketplace sync refuses, and breaches of the house rules for skills, agents and commands |
| `check-skill-crossrefs.py` | Drift between skills: dead links, byte-identical copies, drifted declared copies, stale templates, skills missing from a README, links leaving their plugin, undeclared plugin dependencies, and skill dependencies (`metadata.requires`) that do not match what the skill's files rely on. `--write-requires` adds the missing ones |
| `check-plugin-manifests.py` | Broken `marketplace.json` or `plugin.json`, a `version` in `marketplace.json`, unlisted plugin directories, and — with `--base` — a versioned plugin changed without a version bump |
| `check-secrets.py` | Credentials in tracked files |

## Do not edit the shared checks

Every file in this directory except `local/` comes from the `claude-plugin-knowledge` skill of
the skill-crafting plugin and is kept byte-identical to it. `/claude-plugin-audit` reports a
copy that differs and updates it. A local edit is overwritten by that update; a rule that is
wrong belongs fixed in the skill.

## This repository's own checks: `local/`

- **`local/check-*.py`** — a check only this repository needs. The runner calls each with the
  repository root as its only argument, plus `--base REF` when given; it fails by exiting
  non-zero.
- **`local/copies.json`** — files kept as copies on purpose, where a shared file or a plugin
  dependency cannot reach the consumer (for example a packager that bundles one skill folder on
  its own). `check-skill-crossrefs.py` then stops reporting them as `CLONE` and instead fails
  when a copy differs from its original:

  ```json
  {
    "copies": [
      {
        "original": "plugins/a/skills/x/references/f.md",
        "copies": ["plugins/b/skills/y/references/f.md"],
        "reason": "Packager Z bundles one skill folder on its own"
      }
    ]
  }
  ```
