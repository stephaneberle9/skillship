"""Deliver a distro into a recipient's git repository: one signed commit, pushed or proposed.

`deliver` clones the target branch and applies the delivery to the clone by
ownership class, the same three classes `.skillship.json` records:

| Class    | Paths                                              | On delivery |
| -------- | -------------------------------------------------- | ----------- |
| `owned`  | shipped skill files, generated `plugin.json` and README, owned target files | Replaced; removed when they stop shipping; drift-checked |
| `seeded` | seeded target files, a `.gitlab-ci.yml` it creates | Created when missing, never overwritten; a newer source is announced |
| `merged` | `.claude-plugin/marketplace.json`                  | Only the delivered plugins' entries are added, updated or removed |

Everything else in the target repo is never written. Before applying, every
`owned` file whose content differs from the last delivery's record is drift: the
recipient changed it, and `deliver` stops until it is merged back with
`skillship drift` or discarded with `--overwrite`.

When the source template of a `seeded` file changed since the recipient last
heard of it, the delivery commit, and the pull or merge request, carry a patch
from that version to the new one, for the recipient to apply if they want it.
The record's `source-sha256` names the version announced last, so each change is
announced once.

After committing, `deliver` runs each target-file set's `gate`, so a delivery
that would fail the recipient's checks is never pushed. The gate runs in the
source repo and reads the clone as data (`{target}`): nothing of the
recipient's runs on the sender's machine, where the sender's credentials and
signing key are.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from skillship import ci, config, gitops, lockfile
from skillship import profile as profile_mod
from skillship.build import RECORD, DeliveredFile, delivered, json_bytes, normalized
from skillship.errors import SkillshipError
from skillship.layout import MANIFEST, MARKETPLACE, Layout
from skillship.profile import Profile
from skillship.source import SourceRepo

GITLAB_CI = ".gitlab-ci.yml"
_BODY_LIMIT = 60_000  # GitHub caps a PR description at 65,536 characters
_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass
class Delivery:
    """Everything one `git` target receives, but `.skillship.json`."""

    files: dict[str, DeliveredFile]
    layout: Layout
    plugins: list[str]
    gates: list[tuple[str, str]]  # (set, command)


@dataclass
class Drifted:
    path: str
    change: str  # modified | deleted


@dataclass
class SeededUpdate:
    """A seeded file whose source template changed since the recipient last heard of it."""

    path: str  # in the target repo
    source: str  # the template in the source repo
    patch: (
        bytes | None
    )  # from the version announced last; None when it cannot be rebuilt


@dataclass
class Announcement:
    """A target file the recipient already had, with the text they are shown for it."""

    path: str  # in the target repo
    source: str  # the announced file in the source repo
    text: bytes


@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    seeded: list[SeededUpdate] = field(default_factory=list)
    announced: list[Announcement] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.lines.append(line)


def delivery(
    repo: SourceRepo, profile: Profile, lock: dict[str, Any], forge: str | None
) -> Delivery:
    """The delivered files of the lock, with the target files for a target on `forge`."""
    files = delivered(repo, profile, lock)
    gates = []
    for name, spec in (lock.get("target-files") or {}).items():
        for entry in spec.get("files", []):
            if entry.get("forge") and entry["forge"] != forge:
                continue
            path = entry["path"]
            parts = (
                entry["from"] if isinstance(entry["from"], list) else [entry["from"]]
            )
            announce = entry.get("announce")
            _check_path(path)
            if path in files or path == RECORD:
                raise SkillshipError(
                    f"target file `{path}` of set `{name}` lands on a delivered file"
                )
            for src in [*parts, *([announce] if announce else [])]:
                if not repo.is_tracked_file(src):
                    raise SkillshipError(
                        f"the lock delivers `{src}` as `{path}`, which is no file of the source repo"
                    )
            if (len(parts) > 1 or announce) and entry["ownership"] != "seeded":
                raise SkillshipError(
                    f"the lock joins files into or announces `{path}`, which is not seeded"
                )
            mode = int(entry["mode"], 8) if entry.get("mode") else repo.mode(parts[0])
            data = b"".join(normalized((repo.root / p).read_bytes()) for p in parts)
            files[path] = DeliveredFile(
                data,
                mode,
                entry["ownership"],
                " + ".join(parts),
                tuple(parts),
                announce,
            )
        if spec.get("gate"):
            gates.append((name, spec["gate"]))
    layout = Layout.from_lock(lock)
    plugins = [p["name"] for p in lock.get("plugins", [])]
    return Delivery(files, layout, plugins, gates)


def base_delivery(
    repo: SourceRepo,
    profile: Profile,
    record: dict[str, Any],
    forge: str | None,
    where: Path,
) -> tuple[SourceRepo, Delivery] | None:
    """The source repo at the commit a record names, and the delivery it made; None when unknown.

    `where` receives a worktree of that commit; `remove_worktree` takes it away.
    The record is the recipient's to edit, so its commit counts only when it is
    a full object id of this source repo, and an ancestor of what is delivered
    now: never a ref, an option, or an unrelated commit picked for what its
    delivery held.
    """
    if record.get("dirty"):
        return None
    name, _, sha = str(record.get("source", "")).rpartition("@")
    if (
        name != repo.name()
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha)
        or not gitops.has_commit(repo.root, sha)
        or gitops.run(
            repo.root, "git", "merge-base", "--is-ancestor", sha, "HEAD", check=False
        ).returncode
        != 0
    ):
        return None
    gitops.git(repo.root, "worktree", "add", "--quiet", "--detach", str(where), sha)
    try:
        base_repo = SourceRepo(where)
        base_profile = profile_mod.load(where, profile.name)
        lock, _ = lockfile.load(where, profile.name)
        return base_repo, delivery(base_repo, base_profile, lock, forge)
    except SkillshipError:
        return None  # the commit lacks the distro, or holds a lock this version cannot read


def remove_worktree(repo: SourceRepo, where: Path) -> None:
    if where.exists():
        gitops.git(repo.root, "worktree", "remove", "--force", str(where), check=False)
        gitops.git(repo.root, "worktree", "prune", check=False)


def _check_path(path: str) -> None:
    p = PurePosixPath(path)
    if (
        not p.parts
        or p.is_absolute()
        or ".." in p.parts
        # Windows reads `.GIT`, `.git.` and `.git ` as `.git`
        or any(part.lower().rstrip(". ") == ".git" for part in p.parts)
    ):
        raise SkillshipError(f"`{path}` leaves the target repo")


def inside(clone: Path, path: str) -> Path:
    """`clone / path`, refused when it leaves the clone or passes through a symlink.

    The clone is the recipient's repository, so any component of a delivered
    path may be a symlink the recipient committed; following it would let a
    delivery read, write or delete files anywhere on this machine.
    """
    _check_path(path)
    current = clone
    for part in PurePosixPath(path).parts:
        current = current / part
        if current.is_symlink():
            raise SkillshipError(
                f"`{path}` passes through a symlink in the target repo; "
                "skillship does not follow symlinks there"
            )
    resolved = current.resolve()
    if not resolved.is_relative_to(clone.resolve()) or resolved.is_relative_to(
        (clone / ".git").resolve()  # also an alias, such as an 8.3 short name
    ):
        raise SkillshipError(f"`{path}` leaves the target repo")
    return current


def read_record(clone: Path, name: str) -> dict[str, Any]:
    path = inside(clone, RECORD)
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SkillshipError(f"the target's {RECORD} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        raise SkillshipError(f"the target's {RECORD} is not a skillship record")
    if record.get("distro") != name:
        raise SkillshipError(
            f"the target holds the delivery of distro `{record.get('distro')}`, not `{name}`; "
            "one target repo receives one distro"
        )
    return record


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(path: str, data: bytes) -> str:
    """The hash `.skillship.json` records: a `plugin.json`'s without its `version`.

    The recipient may version a delivered plugin; raising the version is no change
    to merge back, and `deliver` keeps it.
    """
    if path == MANIFEST or path.endswith("/" + MANIFEST):
        try:
            manifest = json.loads(data)
        except ValueError:
            return sha256(data)
        if isinstance(manifest, dict):
            manifest.pop("version", None)
            return sha256(json_bytes(manifest))
    return sha256(data)


def drift(clone: Path, record: dict[str, Any]) -> list[Drifted]:
    """The `owned` files of the last delivery the recipient changed or deleted since."""
    out = []
    for path, meta in sorted(record.get("files", {}).items()):
        if meta.get("ownership") != "owned":
            continue
        _check_path(path)
        file = inside(clone, path)
        if not file.is_file():
            out.append(Drifted(path, "deleted"))
        elif content_hash(path, normalized(file.read_bytes())) != meta.get("sha256"):
            out.append(Drifted(path, "modified"))
    return out


def _taken_back(clone: Path, d: Drifted, new: Delivery) -> bool:
    """Whether the new delivery already holds the recipient's change, so nothing is lost."""
    f = new.files.get(d.path)
    if d.change == "deleted":
        return f is None
    if f is None:
        return False
    theirs = normalized(inside(clone, d.path).read_bytes())
    return content_hash(d.path, f.data) == content_hash(d.path, theirs)


def deliver_git(
    repo: SourceRepo,
    profile: Profile,
    lock: dict[str, Any],
    lock_digest: str,
    target: dict[str, Any],
    *,
    dry_run: bool = False,
    overwrite: list[str] | None = None,
) -> Outcome:
    """Deliver into one `git` target, with the CI credentials when they are set (see `ci`)."""
    with ci.credentials(target["url"], config.forge_of(target)) as ci_ident:
        # the CI identity and key come last, so they win over the source repo's
        ident = identity(repo.root) + (ci_ident or [])
        return _deliver_git(
            repo, profile, lock, lock_digest, target, dry_run, overwrite, ident
        )


def _deliver_git(
    repo: SourceRepo,
    profile: Profile,
    lock: dict[str, Any],
    lock_digest: str,
    target: dict[str, Any],
    dry_run: bool,
    overwrite: list[str] | None,
    ident: list[str],
) -> Outcome:
    out = Outcome()
    forge = config.forge_of(target)
    mode = target.get("mode", "push")
    if mode == "pull-request" and forge != "github":
        raise SkillshipError(
            f"{target['url']}: `mode: pull-request` needs a GitHub target (`forge: github`)"
        )
    if mode == "merge-request" and forge != "gitlab":
        raise SkillshipError(
            f"{target['url']}: `mode: merge-request` needs a GitLab target (`forge: gitlab`)"
        )
    sha, dirty = repo.head()
    if not sha:
        raise SkillshipError("the source repo has no commit to deliver from")
    if dirty and not dry_run:
        raise SkillshipError(
            "the source repo has uncommitted changes -- commit them first, so the "
            "delivery records a source commit that reproduces it"
        )
    source = f"{repo.name()}@{sha}"
    new = delivery(repo, profile, lock, forge)

    tmp = Path(tempfile.mkdtemp(prefix="skillship-"))
    try:
        clone = tmp / "target"
        branch = gitops.clone(target["url"], target.get("branch"), clone)
        old = read_record(clone, profile.name)
        drifted = [
            d
            for d in drift(clone, old)
            if d.path not in (overwrite or []) and not _taken_back(clone, d, new)
        ]
        if drifted:
            lines = [f"  {d.path} ({d.change})" for d in drifted]
            raise SkillshipError(
                f"{target['url']}: the recipient changed delivered files since the last delivery:\n"
                + "\n".join(lines)
                + f"\nRun `skillship drift {profile.name}` to merge the changes back into "
                "the source, or discard them with `--overwrite PATH`."
            )
        # Which files to remove, and which marketplace entries to drop, comes from
        # the last delivery as rebuilt from the source, never from the record alone:
        # anyone with write access to the target can edit `.skillship.json`, and a
        # forged entry must not make a signed delivery delete the recipient's files.
        base = base_delivery(repo, profile, old, forge, tmp / "base") if old else None
        record = _apply(
            repo, sha, clone, profile, new, old, forge, out, base[1] if base else None
        )
        record.update(
            {"distro": profile.name, "source": source, "lock-digest": lock_digest}
        )
        inside(clone, RECORD).write_bytes(
            (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
        )

        gitops.git(clone, "add", "-A")
        # the executable bit, also where the file system has none (Windows)
        for path, meta in record["files"].items():
            f = new.files.get(path)
            written = meta["ownership"] == "owned" or path not in old.get("files", {})
            if f and f.mode & 0o111 and written:
                gitops.git(clone, "update-index", "--chmod=+x", "--", path)
        changed = gitops.git(
            clone, "diff", "--cached", "--name-status", "--", ".", f":!{RECORD}"
        )
        if not changed.strip() and record["files"] == old.get("files"):
            out.say(f"{target['url']}: up to date, nothing to deliver")
            return out
        _commit(
            clone,
            profile,
            source,
            changed,
            out.seeded,
            out.announced,
            sign=not dry_run,
            ident=ident,
        )
        for name, command in new.gates:
            _gate(repo.root, clone, name, command, f"origin/{branch}")
        stat = gitops.git(
            clone, "show", "--stat=1000", "--stat-graph-width=20", "--format=", "HEAD"
        ).rstrip()
        if dry_run:
            out.say(f"{target['url']}: would deliver to `{branch}` ({mode}):")
            out.lines += stat.splitlines()
            return out
        _push(clone, profile, target, forge, mode, branch, source, out)
        out.lines += stat.splitlines()
        return out
    finally:
        remove_worktree(repo, tmp / "base")
        rmtree(tmp)


def _apply(
    repo: SourceRepo,
    sha: str,
    clone: Path,
    profile: Profile,
    new: Delivery,
    old: dict[str, Any],
    forge: str | None,
    out: Outcome,
    previous: Delivery | None,
) -> dict[str, Any]:
    """Write the delivery into the clone; returns the new record (without its header).

    `previous` is the last delivery as rebuilt from the source; only its files
    are removed when they no longer ship.
    """
    old_files: dict[str, dict[str, Any]] = old.get("files", {})
    files = dict(new.files)
    _bump_versions(clone, profile, new, files, old_files, out)
    record: dict[str, Any] = {"files": {}}

    for path, f in sorted(files.items()):
        target = inside(clone, path)
        if f.ownership == "owned":
            mine = path in old_files
            if (
                not mine
                and target.is_file()
                and normalized(target.read_bytes()) != f.data
            ):
                out.say(f"  `{path}` was the recipient's and is replaced")
            _write(target, f)
            record["files"][path] = {
                "ownership": "owned",
                "sha256": content_hash(path, f.data),
            }
        elif f.ownership == "seeded":
            seeded = old_files.get(path, {})
            if seeded.get("ownership") == "seeded":
                entry = dict(seeded)
                announced = entry.get("source-sha256", entry.get("sha256"))
                if f.source and announced != sha256(f.data):
                    entry["source-sha256"] = sha256(f.data)
                    patch = _seeded_patch(repo, path, f, entry, old)
                    if patch != b"":  # b"": back at the seeded version, nothing to take
                        out.seeded.append(SeededUpdate(path, f.source, patch))
                        out.say(
                            f"  seeded `{path}` has a newer template in the source "
                            f"(`{f.source}`); the delivery carries the "
                            + ("patch" if patch else "news")
                            + " for the recipient"
                        )
                record["files"][path] = entry
            elif target.exists():
                out.say(f"  `{path}` exists in the target and is left as it is")
                if f.announce:
                    text = normalized((repo.root / f.announce).read_bytes())
                    entry = {"ownership": "announced", "sha256": sha256(text)}
                    if seeded != entry:
                        out.announced.append(Announcement(path, f.announce, text))
                        out.say(
                            f"  the delivery shows the recipient `{f.announce}` "
                            f"for their own `{path}`"
                        )
                    record["files"][path] = entry
            else:
                _write(target, f)
                record["files"][path] = {
                    "ownership": "seeded",
                    "sha256": sha256(f.data),
                    "seeded-from": sha,
                }
        else:  # merged
            listed = previous.plugins if previous else []
            data = _merge_marketplace(target, f.data, listed)
            _write(target, DeliveredFile(data, f.mode, "merged"))
            record["files"][path] = {"ownership": "merged", "sha256": sha256(data)}

    for path, meta in sorted(old_files.items()):
        if path in files:
            continue
        if meta.get("ownership") == "owned":
            if previous is None or path not in previous.files:
                out.say(
                    f"  `{path}` no longer ships, but the source does not confirm an "
                    "earlier delivery wrote it; left in place"
                )
                continue
            inside(clone, path).unlink(missing_ok=True)
            _prune_dirs(clone, inside(clone, path).parent)
        elif meta.get("ownership") == "seeded" and path == GITLAB_CI:
            record["files"][path] = meta

    if forge == "gitlab":
        _gitlab_include(clone, files, old_files, record, out)
    if new.plugins:
        record["plugins"] = sorted(new.plugins)
    return record


def _write(target: Path, f: DeliveredFile) -> None:
    if target.is_symlink():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(f.data)
    if f.mode & 0o111:
        target.chmod(target.stat().st_mode | 0o111)


def _prune_dirs(clone: Path, d: Path) -> None:
    while d != clone and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def _merge_marketplace(target: Path, ours: bytes, previous: list[str]) -> bytes:
    """The recipient's `marketplace.json` with the delivered plugins' entries set.

    Entries of plugins an earlier delivery listed and this one does not are
    removed; every other entry and field stays the recipient's.
    """
    mine = json.loads(ours)
    if not target.is_file():
        return ours
    try:
        theirs = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SkillshipError(
            f"the target's {MARKETPLACE} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(theirs, dict) or not isinstance(theirs.get("plugins", []), list):
        raise SkillshipError(f"the target's {MARKETPLACE} has no `plugins` list")
    entries = {e["name"]: e for e in mine["plugins"]}
    drop = set(previous) | set(entries)
    kept = [
        e
        for e in theirs.get("plugins", [])
        if not (isinstance(e, dict) and e.get("name") in drop)
    ]
    theirs["plugins"] = kept + [entries[n] for n in sorted(entries)]
    for key in ("name", "owner"):
        theirs.setdefault(key, mine[key])
    return json_bytes(theirs)


def _bump_versions(
    clone: Path,
    profile: Profile,
    new: Delivery,
    files: dict[str, DeliveredFile],
    previous: dict[str, Any],
    out: Outcome,
) -> None:
    """Keep the version of a plugin the recipient versions, raised by a patch when its files change."""
    for plugin in new.plugins:
        rel = new.layout.plugin_file(plugin, MANIFEST)
        current = inside(clone, rel)
        if not current.is_file() or rel not in files:
            continue
        try:
            version = json.loads(current.read_text(encoding="utf-8")).get("version")
        except ValueError:
            continue
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            continue
        manifest = json.loads(files[rel].data)
        if profile.version == "bump-patch" and _plugin_changed(
            clone, new, plugin, files, rel, previous
        ):
            major, minor, patch = (int(x) for x in version.split("."))
            manifest["version"] = f"{major}.{minor}.{patch + 1}"
            out.say(f"  {plugin}: version {version} -> {manifest['version']}")
        else:
            manifest["version"] = version
        f = files[rel]
        files[rel] = DeliveredFile(json_bytes(manifest), f.mode, f.ownership, f.source)


def _plugin_changed(
    clone: Path,
    new: Delivery,
    plugin: str,
    files: dict[str, DeliveredFile],
    manifest: str,
    previous: dict[str, Any],
) -> bool:
    prefix = new.layout.plugin_file(plugin, "")
    ours = {p: f for p, f in files.items() if p.startswith(prefix) and p != manifest}
    for path, f in ours.items():
        target = inside(clone, path)
        if not target.is_file() or normalized(target.read_bytes()) != f.data:
            return True
    # a file of the last delivery that stops shipping is removed: a change too
    return any(
        path.startswith(prefix)
        and path not in ours
        and path != manifest
        and meta.get("ownership") == "owned"
        and inside(clone, path).is_file()
        for path, meta in previous.items()
    )


def _gitlab_include(
    clone: Path,
    files: dict[str, DeliveredFile],
    old_files: dict[str, Any],
    record: dict[str, Any],
    out: Outcome,
) -> None:
    """Make GitLab run delivered CI job files: create `.gitlab-ci.yml`, or say what to add."""
    jobs = sorted(p for p in files if re.fullmatch(r"\.gitlab/ci/[^/]+\.ya?ml", p))
    if not jobs:
        return
    ci = inside(clone, GITLAB_CI)
    if not ci.exists():
        text = "include:\n" + "".join(f"  - local: {j}\n" for j in jobs)
        _write(ci, DeliveredFile(text.encode(), 0o644, "seeded"))
        record["files"][GITLAB_CI] = {
            "ownership": "seeded",
            "sha256": sha256(text.encode()),
        }
        return
    if old_files.get(GITLAB_CI, {}).get("ownership") == "seeded":
        record["files"][GITLAB_CI] = old_files[GITLAB_CI]
    text = ci.read_text(encoding="utf-8", errors="replace")
    missing = [j for j in jobs if j not in text]
    if missing:
        out.say(
            f"  {GITLAB_CI} is the recipient's; add this so GitLab runs the delivered jobs:\n"
            "    include:\n"
            + "".join(f"      - local: {j}\n" for j in missing).rstrip()
        )


# The identity and signing settings a delivery commit takes from the source repo.
# The clone lives in a temporary directory, where a git configuration that
# differs by directory (`includeIf "gitdir:..."`) would not apply, so the commit
# is signed and authored as a commit in the source repo would be.
IDENTITY_KEYS = (
    "user.name", "user.email", "user.signingkey", "gpg.format", "gpg.program",
    "gpg.ssh.program", "gpg.openpgp.program", "gpg.x509.program",
)  # fmt: skip


def identity(source_root: Path) -> list[str]:
    """`-c key=value` arguments carrying the source repo's identity and signing settings."""
    out = []
    for key in IDENTITY_KEYS:
        value = gitops.git(source_root, "config", "--get", key, check=False).strip()
        if value:
            out += ["-c", f"{key}={value}"]
    return out


def _seeded_patch(
    repo: SourceRepo,
    path: str,
    f: DeliveredFile,
    entry: dict[str, Any],
    old: dict[str, Any],
) -> bytes | None:
    """The patch of `path` from the version originally seeded to the template `f`.

    The base is the seeded version, not the one announced last, because the
    recipient's history surely holds it, whether or not they applied an earlier
    patch: `git apply -3` finds it there by the patch's `index` line and merges
    three-way. It is the template at the commit the file was seeded from (for a
    record older than `seeded-from`, at the last delivery's commit), checked
    against the recorded hash; None when it cannot be rebuilt.
    """
    candidates = [
        entry.get("seeded-from"),
        str(old.get("source", "")).rpartition("@")[2],
    ]
    for sha in filter(None, candidates):
        if not gitops.has_commit(repo.root, sha):
            continue
        parts = f.parts or ((f.source,) if f.source else ())
        seeded = _at_commit(repo, sha, parts)
        if seeded is not None and sha256(seeded) == entry.get("sha256"):
            return gitops.patch(path, seeded, f.data)
    return None


def _at_commit(repo: SourceRepo, sha: str, parts: tuple[str, ...]) -> bytes | None:
    """The source files `parts` at commit `sha`, joined; None when one is missing there."""
    if not parts:
        return None
    out = b""
    for part in parts:
        done = gitops.run_bytes(repo.root, "git", "show", f"{sha}:{part}")
        if done.returncode != 0:
            return None
        out += normalized(done.stdout)
    return out


def seeded_notes(updates: list[SeededUpdate], source: str, *, markdown: bool) -> str:
    """The section of a commit message or PR/MR description that announces `updates`."""
    if not updates:
        return ""
    name = source.rpartition("@")[0]
    parts = [
        "## Seeded files with a newer template"
        if markdown
        else "Seeded files with a newer template",
        "",
        (
            "These files were seeded by an earlier delivery and are yours: skillship "
            f"never changes them. Their template in {name} changed since. Each patch "
            "below goes from the version originally seeded into your repository to "
            "the template as it is now, so it may repeat changes announced before."
        ),
        "",
        (
            "To take a change, save its patch to a file and run `git apply -3 <file>` "
            "in your repository. `-3` makes a three-way merge against the originally "
            "seeded version, which your repository's history holds (the patch's "
            "`index` line names it): git compares your file and the new template each "
            "with that version, keeps your edits, and takes the template's changes "
            "where they touch other lines; a change you took from an earlier patch "
            "counts as yours and is not applied twice. Where your file and the "
            "template changed the same or adjacent lines, it leaves conflict markers "
            "for you to resolve."
        ),
    ]
    for u in updates:
        parts += [
            "",
            f"`{u.path}`, from `{u.source}`:"
            if markdown
            else f"{u.path}, from {u.source}:",
        ]
        if u.patch is None:
            parts += [
                "",
                f"(no patch: the originally seeded version cannot be rebuilt; compare with `{u.source}` at {source})",
            ]
            continue
        text = u.patch.decode("utf-8", errors="replace").rstrip("\n")
        if markdown:
            longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
            fence = "`" * max(3, longest + 1)
            parts += ["", f"{fence}diff", text, fence]
        else:
            parts += ["", text]
    return "\n".join(parts) + "\n"


def announced_notes(items: list[Announcement], *, markdown: bool) -> str:
    """The section of a commit message or PR/MR description that shows `items`."""
    if not items:
        return ""
    parts = [
        "## Files you already had" if markdown else "Files you already had",
        "",
        (
            "skillship would have created these files, but your repository already "
            "has its own, which skillship never changes. Add what follows to each "
            "of them yourself, where it fits; it is shown again only when it changes."
        ),
    ]
    for a in items:
        text = a.text.decode("utf-8", errors="replace").strip("\n")
        parts += [
            "",
            f"`{a.path}`, from `{a.source}`:"
            if markdown
            else f"{a.path}, from {a.source}:",
            "",
        ]
        if markdown:
            longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
            fence = "`" * max(3, longest + 1)
            parts += [fence, text, fence]
        else:
            parts.append(text)
    return "\n".join(parts) + "\n"


def _notes(
    seeded: list[SeededUpdate],
    announced: list[Announcement],
    source: str,
    *,
    markdown: bool,
) -> str:
    sections = [
        seeded_notes(seeded, source, markdown=markdown),
        announced_notes(announced, markdown=markdown),
    ]
    return "\n".join(s for s in sections if s)


def _commit(
    clone: Path,
    profile: Profile,
    source: str,
    changed: str,
    seeded: list[SeededUpdate],
    announced: list[Announcement],
    *,
    sign: bool,
    ident: list[str],
) -> None:
    name, _, sha = source.rpartition("@")
    counts: dict[str, int] = {}
    for line in changed.splitlines():
        status = line[:1]
        counts[status] = counts.get(status, 0) + 1
    words = {"A": "added", "M": "changed", "D": "removed"}
    summary = (
        ", ".join(
            f"{n} {words.get(s, s)}"
            for s, n in sorted(counts.items(), key=lambda x: "AMD".find(x[0]))
        )
        or "no delivered file changed, only the record"
    )
    message = (
        f"chore: deliver {profile.name} from {name}@{sha[:12]}\n\n"
        f"Delivered by skillship from {source}: {summary}.\n"
    )
    notes = _notes(seeded, announced, source, markdown=False)
    if notes:
        message += "\n" + notes
    # verbatim: the default cleanup strips trailing blanks a patch line may need
    args = [*ident, "commit", "--quiet", "--cleanup=verbatim", "-F", "-"]
    if sign:
        args.insert(len(ident) + 1, "-S")
    else:
        args = ["-c", "commit.gpgsign=false", *args]
    done = gitops.run(clone, "git", *args, check=False, input=message)
    if done.returncode != 0:
        raise SkillshipError(
            f"cannot commit the delivery:\n{(done.stderr or done.stdout).strip()}"
        )


def _gate(root: Path, clone: Path, name: str, command: str, base: str) -> None:
    """Run a gate in the source repo `root`, against the target clone.

    Never in the clone: a file the recipient planted there (a module next to a
    script, a `uv.toml`) would run with the sender's credentials.
    """
    argv = [
        arg.replace("{base}", base).replace("{target}", clone.as_posix())
        for arg in shlex.split(command)
    ]
    print(f"gate `{name}`: {shlex.join(argv)}", flush=True)
    try:
        # the gate is the source repo's code and needs no credentials
        done = subprocess.run(argv, cwd=root, env=ci.scrubbed_env(), check=False)
    except OSError as exc:
        raise SkillshipError(f"cannot run the `{name}` gate: {exc}") from exc
    if done.returncode != 0:
        raise SkillshipError(
            f"the `{name}` gate failed on the target clone (exit {done.returncode}); "
            "nothing was pushed"
        )


def _push(
    clone: Path,
    profile: Profile,
    target: dict[str, Any],
    forge: str | None,
    mode: str,
    branch: str,
    source: str,
    out: Outcome,
) -> None:
    if mode == "push":
        gitops.git(clone, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
        out.say(f"{target['url']}: pushed to `{branch}`")
        return
    head = f"skillship/{profile.name}"
    gitops.git(clone, "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{head}")
    assert forge
    body = (
        f"Delivered by skillship from `{source}`.\n\n"
        f"The branch `{head}` is rewritten by every delivery until this is merged.\n"
    )
    notes = _notes(out.seeded, out.announced, source, markdown=True)
    if len(body) + len(notes) > _BODY_LIMIT:
        names = ", ".join(f"`{x.path}`" for x in [*out.seeded, *out.announced])
        notes = (
            f"\n## Files to update yourself\n\n{names}: what to change in them is "
            "too long for this description and is in the delivery commit's message.\n"
        )
    elif notes:
        notes = "\n" + notes
    url = gitops.open_pull_request(
        clone, forge, branch, head, f"chore: deliver {profile.name}", body + notes
    )
    kind = "pull request" if forge == "github" else "merge request"
    out.say(f"{target['url']}: pushed `{head}`, {kind} {url}")


def rmtree(path: Path) -> None:
    """Remove a directory, including the read-only files git leaves behind on Windows."""

    def force(func: Any, p: str, _exc: Any) -> None:
        Path(p).chmod(0o700)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=force)
