# skillship plugin <!-- omit in toc -->

Draft, lock and deliver distros of your Agent Skills with skillship:
dangling-report walk-throughs, lock-diff explanations, CI delivery setup,
recipient drift and Agensi submission. Designed for [Claude
Code](https://claude.com/product/claude-code).

- [Installation \& Updates](#installation--updates)
- [What It Does](#what-it-does)
- [Skills](#skills)
- [Example Workflows](#example-workflows)

## Installation & Updates

**Install:**

```text
/plugin marketplace add stephaneberle9/skillship
/plugin install skillship@skillship
```

**Update:** `/plugin marketplace update skillship`, or turn on automatic updates
for the marketplace in `/plugin`.

The skill runs the CLI through `uvx` at the version pinned in your source repo's
`distros/.skillship-version`, so it needs [uv](https://docs.astral.sh/uv/).
Delivery setup from CI uses the GitHub CLI (`gh`).

## What It Does

The plugin puts the [skillship](../../README.md) CLI in Claude's hands for the
parts that need judgment: which skills a recipient gets, what a dangling
reference means, whether a recipient's change belongs upstream. It never pushes
a delivery, commits or submits to a marketplace unless you ask, and no secret
passes through the conversation.

- **Drafting** — a distro profile from a one-sentence request
- **Locking** — the dangling report, one entry at a time, with the profile edit
  or source rewording that fixes it
- **Reviewing** — a lock diff, as what the recipient gains or loses
- **Uploading** — `skill-upload` or `plugin-upload`, decided by the paths an
  upload would break
- **CI delivery** — deploy token, delivery-only signing key, source-repo secrets
  and a dry run, step by step
- **Drift** — the recipient's changes, patch by patch, merged back or discarded
- **Agensi** — pre-flight review and submission through Agensi's MCP connector,
  after you confirm the preview

## Skills

| Skill | Description |
| --- | --- |
| `/skillship` | Drive skillship: draft a distro, lock it and walk through the dangling report, explain a lock diff, choose an upload target, set up CI delivery to a git target, go through recipient drift, and submit an Agensi pack. |

## Example Workflows

### A First Distro

```text
You: Give ACME the billing skills as a plugin in their GitLab repo, through merge requests.

Claude: Drafts distros/acme/distro.yaml (include billing:invoice-*, a merged plugin,
       a git target in merge-request mode) → runs lock → goes through the two
       blocking mentions with you → dry-runs the delivery
```

### A Failing Lock Check

```text
You: CI says the acme lock is stale.

Claude: Recomputes the lock → "ACME would now also receive tax-report, because
       invoice-export started requiring it" → you decide: exclude it or ship it
```

### Recipient Drift

```text
You: The delivery to ACME stopped on drift.

Claude: Runs skillship drift acme → shows the one clean patch → applies it to the
       source on your yes → the next delivery goes through
```
