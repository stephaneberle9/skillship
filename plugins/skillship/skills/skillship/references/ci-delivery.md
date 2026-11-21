# Delivering a git target from GitHub Actions

Set up once per distro. Each step says **where** it happens: the **target repo**
(the recipient's forge), the **committer account** (whose name the delivery
commits carry), or the **source repo** (where the workflow runs). Walk the user
through them in this order, one at a time, and confirm each before moving on.
The user creates every credential and pastes every secret themselves; you never
see a value.

`<KEY>` is the distro's name upper-cased with `_` for `-` (`acme-billing` →
`ACME_BILLING`); `skillship list --json` prints it.

## 1. The workflow in the source repo

Check for `.github/workflows/*.yml` calling
`stephaneberle9/skillship/.github/workflows/distros.yml` (from skillship
0.6.0; `distro.yml` before) and for `distros/.skillship-version`. If either is
missing, copy the caller from
[`templates/deliver-distros.yml`](../templates/deliver-distros.yml) to
`.github/workflows/deliver-distros.yml`, adjusting `paths:` to where the source
repo keeps its skills. Pin the reusable workflow by the commit SHA of the
release tag equal to the version in `distros/.skillship-version`, with the tag
as a trailing comment (`@<sha> # v<version>`): the called workflow receives the
source repo's secrets, and a tag can be moved. Look the SHA up with
`gh api repos/stephaneberle9/skillship/commits/v<version> --jq .sha`. Both
pins move together.

Environments (`environments: true` in the caller's `with:`) give each distro its
own secrets and an optional required-reviewer gate. They need a public source
repo, or a paid GitHub plan for a private one; check with
`gh repo view --json visibility` and ask about the plan. Without them, the
per-distro secret names below do the separation.

## 2. Deploy token (target repo)

Ask which forge hosts the target and which `mode` the profile sets.

| Forge, mode | Token | Permissions |
| --- | --- | --- |
| GitHub, `pull-request` | Fine-grained personal access token (*Settings › Developer settings › Fine-grained tokens*), resource owner = the target's owner, *Only select repositories* = the target | *Contents* and *Pull requests*: read and write |
| GitHub, `push` | As above | *Contents*: read and write. A protected branch must let this user push |
| GitLab, `merge-request` | Project access token (*Settings › Access tokens* of the target project) | Scopes `api` and `write_repository`, role Developer or higher |
| GitLab, `push` | Project access token, or a deploy token with `write_repository` | A protected branch needs a role allowed to push there; a deploy token's user name goes into `DISTRO_<KEY>_DEPLOY_USER` |

- An organization may have to approve a fine-grained token before it works on
  its repositories.
- Give every token an expiry, and tell the user to note the date: an expired
  token fails the next delivery at the clone. Rotating is creating a new token
  and setting the secret again.
- On GitLab, the merge request is authored by the token's bot user; that is
  expected.

## 3. Signing key (committer account)

The forge shows a delivery commit as **Verified** when its signature's public
key is registered as a *signing* key on the account that holds the committer
email. That account is the user's own or a bot account they control; on a
customer's self-hosted GitLab it needs an account there.

**Never reuse the key that signs the user's own commits.** A CI secret is
readable by every workflow run and every workflow editor of the source repo. A
leaked personal key signs anything as the user on every forge it is registered
with, and revoking it breaks their local signing too. A delivery-only key costs
one registration per forge account.

**One key per source repo** is the default: stored once as the unprefixed secret
`DISTRO_SIGNING_KEY`, shared by every distro and target of that repo. A key per
target (`DISTRO_<KEY>_SIGNING_KEY`) is the stricter option for a recipient who
warrants it.

Ask SSH or GPG; recommend SSH, which has no user ID or expiry to manage. The
user generates the key in their own terminal, with a passphrase:

- **SSH:**

  ```text
  ssh-keygen -t ed25519 -C "skillship delivery, <source-repo>" -f <path>/skillship-delivery
  ```

- **GPG:**
  `gpg --quick-generate-key "<Name> <committer-email>" ed25519 sign 2y`, then
  `gpg --armor --export-secret-keys <fingerprint>` into a file for step 4, and
  `gpg --armor --export <fingerprint>` for the public half. The user ID's email
  must be the committer email.

Register the public half as a **signing** key on the committer account:

| Forge | SSH | GPG |
| --- | --- | --- |
| GitHub | *Settings › SSH and GPG keys › New SSH key*, key type *Signing Key*; or `gh ssh-key add <file>.pub --type signing` (needs `gh auth refresh -s admin:ssh_signing_key`) | *Settings › SSH and GPG keys › New GPG key* |
| GitLab | *User settings › SSH Keys*, usage type *Signing* (or *Authentication & Signing*) | *User settings › GPG Keys* |

The committer email must be verified on that account. Its `users.noreply`
address works (GitHub `<id>+<user>@users.noreply.github.com`, GitLab
`<id>-<user>@users.noreply.gitlab.com`) and keeps a personal address out of the
recipient's history.

## 4. Secrets and variables (source repo)

The user runs these in their own terminal, in the source repo's checkout.
`gh secret set` without `--body` prompts for the value, so it is pasted there
and nowhere else:

```text
gh secret set DISTRO_<KEY>_DEPLOY_TOKEN
gh secret set DISTRO_SIGNING_KEY < <path>/skillship-delivery
gh secret set DISTRO_SIGNING_KEY_PASSPHRASE
gh variable set DISTRO_COMMITTER_NAME --body "<name>"
gh variable set DISTRO_COMMITTER_EMAIL --body "<committer-email>"
```

- In PowerShell, feed the key file with

  ```text
  Get-Content -Raw <path>/skillship-delivery | gh secret set DISTRO_SIGNING_KEY
  ```

- Add `--env distro-<name>` to each command when the caller sets
  `environments: true`; the environment's secrets then serve as the unprefixed
  names.
- `DISTRO_<KEY>_DEPLOY_USER` is a variable, needed only for a GitLab deploy
  token.
- Afterwards, the private key file belongs in the user's password manager and
  nowhere on disk.
- Check with `gh secret list` and `gh variable list`, which print names only.

## 5. Dry run, then the first delivery

1. Commit the profile, the lock and the workflow when the user asks, and push
   them.
2. Dry run: `gh workflow run deliver-distros.yml -f distro=<name> -f dry-run=true`,
   then `gh run watch`. It clones the target with the token, applies the
   delivery, signs the commit and runs the gate, and pushes nothing. Read the
   job summary with the user.
3. The first real delivery runs only when the user asks: the same command with
   `-f dry-run=false`. Then check the pull or merge request (or the pushed
   commit) and its signature: GitHub
   `gh api repos/<owner>/<repo>/commits/<sha> --jq .commit.verification`, GitLab
   *Verified* badge on the commit.
4. `auto: true` in the profile delivers on every push to the source's default
   branch. Suggest it only after a delivery has gone through by hand, and
   preferably with a reviewed mode.

When the job stops on drift, run `skillship drift <name>` locally (the
SKILL.md's *Recipient drift*); CI never resolves drift.
