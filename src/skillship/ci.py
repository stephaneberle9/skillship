"""Credentials for a delivery where no one is logged in: CI.

Locally, `deliver` and `drift` authenticate through the git credential helper
and the `gh` or `glab` login, and sign with the source repo's git settings. A
CI runner has none of these; the workflow passes them as environment variables:

| Variable                        | Holds |
| ------------------------------- | ----- |
| `DISTRO_DEPLOY_TOKEN`           | A token with write access to the target repo: a fine-grained PAT or a GitHub App token on GitHub, a project access token (or a deploy token, for `push` mode) on GitLab |
| `DISTRO_DEPLOY_USER`            | The user name that goes with the token; default `x-access-token` on GitHub, `oauth2` on GitLab (a deploy token has its own) |
| `DISTRO_SIGNING_KEY`            | An SSH or a GPG private key, told apart by its header |
| `DISTRO_SIGNING_KEY_PASSPHRASE` | The key's passphrase, if it has one |
| `DISTRO_COMMITTER_NAME`, `DISTRO_COMMITTER_EMAIL` | Who the delivery commit is by; the forge shows it as verified when the key's public half is registered with an account holding that email |

When none is set, nothing changes. The token never reaches a command line: git
receives it as an `Authorization` header through `GIT_CONFIG_*` variables,
scoped to the target's host, and `gh` or `glab` as `GH_TOKEN` or
`GITLAB_TOKEN`. The key goes into a temporary directory, removed afterwards: an
SSH key into a file with its passphrase stripped, a GPG key into a throwaway
`GNUPGHOME` whose agent is given the passphrase.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from skillship import gitops
from skillship.errors import SkillshipError

TOKEN = "DISTRO_DEPLOY_TOKEN"
USER = "DISTRO_DEPLOY_USER"
KEY = "DISTRO_SIGNING_KEY"
PASSPHRASE = "DISTRO_SIGNING_KEY_PASSPHRASE"
NAME = "DISTRO_COMMITTER_NAME"
EMAIL = "DISTRO_COMMITTER_EMAIL"
VARIABLES = (TOKEN, USER, KEY, PASSPHRASE, NAME, EMAIL)


def active() -> bool:
    return bool(os.environ.get(TOKEN) or os.environ.get(KEY))


@contextmanager
def credentials(url: str, forge: str | None) -> Generator[list[str] | None]:
    """Set up the CI credentials for one target; yields the commit's `-c` arguments.

    Yields None when no CI variable is set: the caller then uses the source
    repo's identity and the local logins.
    """
    if not active():
        yield None
        return
    tmp = Path(tempfile.mkdtemp(prefix="skillship-ci-"))
    saved = dict(gitops.EXTRA_ENV)
    try:
        env: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}
        token = os.environ.get(TOKEN)
        if token:
            env.update(_token_env(url, forge, token))
        args: list[str] = []
        key = os.environ.get(KEY)
        if key:
            args, key_env = _signing(tmp, key, os.environ.get(PASSPHRASE) or "")
            env.update(key_env)
        name, email = os.environ.get(NAME), os.environ.get(EMAIL)
        if key and not (name and email):
            raise SkillshipError(
                f"{KEY} is set, but {NAME} or {EMAIL} is not: the commit needs an "
                "author the forge can match to the key's account"
            )
        if name and email:
            args = ["-c", f"user.name={name}", "-c", f"user.email={email}", *args]
        gitops.EXTRA_ENV.update(env)
        yield args
    finally:
        gitops.EXTRA_ENV.clear()
        gitops.EXTRA_ENV.update(saved)
        _kill_agent(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


def _token_env(url: str, forge: str | None, token: str) -> dict[str, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SkillshipError(
            f"{TOKEN} authenticates over HTTPS, but the target URL is `{url}` -- "
            "use its https:// form"
        )
    user = os.environ.get(USER) or ("oauth2" if forge == "gitlab" else "x-access-token")
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()
    host = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/"
    env = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"http.{host}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        # no helper may answer instead, or overwrite a stored credential
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
    }
    if forge == "gitlab":
        env["GITLAB_TOKEN"] = token
        env["GITLAB_HOST"] = parts.hostname
    else:
        env["GH_TOKEN"] = token
        if parts.hostname != "github.com":
            env["GH_HOST"] = parts.hostname
    return env


def _signing(tmp: Path, key: str, passphrase: str) -> tuple[list[str], dict[str, str]]:
    key = key.replace("\r\n", "\n").strip() + "\n"
    if "BEGIN OPENSSH PRIVATE KEY" in key:
        return _ssh(tmp, key, passphrase), {}
    if "BEGIN PGP PRIVATE KEY BLOCK" in key:
        return _gpg(tmp, key, passphrase)
    raise SkillshipError(
        f"{KEY} is neither an OpenSSH private key (BEGIN OPENSSH PRIVATE KEY) nor "
        "an armored GPG secret key (BEGIN PGP PRIVATE KEY BLOCK)"
    )


def _ssh(tmp: Path, key: str, passphrase: str) -> list[str]:
    path = tmp / "signing-key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
        fh.write(key)
    if (
        passphrase
    ):  # strip it: `git` signs through `ssh-keygen -Y sign`, which would prompt
        _tool(["ssh-keygen", "-q", "-p", "-P", passphrase, "-N", "", "-f", str(path)])
    return ["-c", "gpg.format=ssh", "-c", f"user.signingkey={path.as_posix()}"]


def _gpg(tmp: Path, key: str, passphrase: str) -> tuple[list[str], dict[str, str]]:
    home = tmp / "gnupg"
    home.mkdir()
    home.chmod(stat.S_IRWXU)
    (home / "gpg-agent.conf").write_text("allow-preset-passphrase\n", encoding="ascii")
    env = {"GNUPGHOME": gpg_home(home)}
    _tool(["gpg", "--batch", "--quiet", "--import"], env, key)
    listing = _tool(
        ["gpg", "--batch", "--with-colons", "--with-keygrip", "--list-secret-keys"], env
    )
    fingerprint, grips = "", []
    for line in listing.splitlines():
        fields = line.split(":")
        if fields[0] == "fpr" and not fingerprint:  # codespell:ignore fpr
            fingerprint = fields[9]
        elif fields[0] == "grp":
            grips.append(fields[9])
    if not fingerprint:
        raise SkillshipError(f"{KEY} holds no secret key gpg can import")
    if passphrase:  # on stdin, never on a command line
        hexed = passphrase.encode().hex().upper()
        script = "".join(f"PRESET_PASSPHRASE {grip} -1 {hexed}\n" for grip in grips)
        _tool(["gpg-connect-agent"], env, script + "/bye\n")
    return (
        ["-c", "gpg.format=openpgp", "-c", f"user.signingkey={fingerprint}"],
        env,
    )


def gpg_home(path: Path) -> str:
    """`path` as the `gpg` on PATH reads a home directory.

    Git for Windows ships an MSYS `gpg`, which takes `/c/Users/...` and reads
    `C:/Users/...` as a relative path; every other `gpg` takes the native form.
    """
    if os.name != "nt":
        return str(path)
    try:
        version = subprocess.run(
            ["gpg", "--version"], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return str(path)
    if not re.search(r"^Home: /", version, re.MULTILINE):
        return str(path)
    drive, rest = os.path.splitdrive(str(path.resolve()))
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


def _tool(
    argv: list[str], env: dict[str, str] | None = None, input: str | None = None
) -> str:
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            input=input,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except OSError as exc:
        raise SkillshipError(f"cannot run `{argv[0]}` to set up {KEY}: {exc}") from exc
    if done.returncode != 0 or re.search(r"^ERR ", done.stdout, re.MULTILINE):
        # the arguments may hold the passphrase; name the tool only
        raise SkillshipError(
            f"`{argv[0]}` failed to set up {KEY}: {(done.stderr or done.stdout).strip()}"
        )
    return done.stdout


def _kill_agent(tmp: Path) -> None:
    home = tmp / "gnupg"
    if home.is_dir():
        subprocess.run(
            ["gpgconf", "--kill", "gpg-agent"],
            env={**os.environ, "GNUPGHOME": gpg_home(home)},
            capture_output=True,
            check=False,
        )


def scrubbed_env() -> dict[str, str]:
    """This process's environment without the CI credentials, for commands that need none."""
    return {k: v for k, v in os.environ.items() if k not in VARIABLES}
