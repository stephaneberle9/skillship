"""Resolve a profile against a source repo: which skills and files ship, and why.

The closure follows `metadata.requires` only. `metadata.suggests` is reported,
never followed. A delivered Markdown file that names a skill which does not
ship is a dangling mention; it blocks the lock unless the skill is suggested
by a shipped skill, the mention is phrased conditionally ("when installed"),
or the profile acknowledges it.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from skillship.errors import SkillshipError
from skillship.profile import Profile
from skillship.source import SourceRepo

LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")

# Wells whose files describe the recipient's repo, not this one; mentions there are examples.
EXEMPLAR_WELLS = ("templates", "examples")

# How a document relies on a skill by name: `/skill`, `plugin:skill`, or
# "the `skill` skill". A bare backticked name is an illustration. The same
# rule as the plugin checks' DEPEND verdict, so both agree on what a mention is.
_MENTION = re.compile(r"`(/)?(?:([a-z0-9-]+):)?([a-z0-9-]+)`")
_CONDITIONAL = re.compile(
    r"\b(?:(?:when|if)\s+installed|(?:wenn|falls|sofern)\s+installiert)\b",
    re.IGNORECASE,
)
_PARAGRAPH = re.compile(r"(?:[^\n]*\S[^\n]*(?:\n|$))+")


@dataclass
class Dangling:
    ref: str
    at: str
    status: str  # acknowledged | suggested | conditional | blocking


@dataclass
class Resolution:
    skills: dict[str, str] = field(
        default_factory=dict
    )  # id -> reason, in resolution order
    parent: dict[str, str] = field(
        default_factory=dict
    )  # id -> the skill that required it
    files: dict[str, list[str]] = field(default_factory=dict)
    extras: list[str] = field(
        default_factory=list
    )  # repo-root files that ship, e.g. LICENSE
    suggested: dict[str, list[str]] = field(
        default_factory=dict
    )  # ref -> shipped skills suggesting it
    dangling: list[Dangling] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Dangling]:
        return [d for d in self.dangling if d.status == "blocking"]


def resolve(repo: SourceRepo, profile: Profile) -> Resolution:
    res = Resolution()
    queue: deque[str] = deque()
    for pattern in profile.include:
        ids = repo.match(pattern)
        if not ids:
            res.errors.append(f"`include: {pattern}` matches no skill")
        for sid in ids:
            if sid not in res.skills:
                res.skills[sid] = f"include {pattern}"
                queue.append(sid)
    while queue:
        sid = queue.popleft()
        for dep in repo.skills[sid].requires:
            if dep not in res.skills:
                res.skills[dep] = f"required by {sid}"
                res.parent[dep] = sid
                queue.append(dep)

    for pattern in profile.exclude:
        ids = repo.match(pattern)
        if not ids:
            res.errors.append(f"`exclude: {pattern}` matches no skill")
        for sid in ids:
            if sid in res.skills:
                res.errors.append(f"`{sid}` is excluded, but ships: {_chain(res, sid)}")

    if profile.shape == "skills":
        by_name: dict[str, list[str]] = defaultdict(list)
        for sid in res.skills:
            by_name[repo.skills[sid].name].append(sid)
        for name, ids in sorted(by_name.items()):
            if len(ids) > 1:
                res.errors.append(
                    f"shape `skills` puts {', '.join(f'`{i}`' for i in sorted(ids))} "
                    f"into the same directory `skills/{name}/`"
                )

    for sid in res.skills:
        res.files[sid] = repo.files(repo.skills[sid], profile.file_excludes)
    if profile.license == "inherit":
        res.extras = [
            f
            for f in LICENSE_FILES
            if (repo.root / f).is_file() and not repo.is_symlink(f)
        ]

    for sid in res.skills:
        for ref in repo.skills[sid].suggests:
            if ref not in res.skills:
                res.suggested.setdefault(ref, []).append(sid)

    res.dangling = _dangling(repo, profile, res)
    acknowledged = {d.ref for d in res.dangling if d.status == "acknowledged"}
    for pattern in profile.acknowledge:
        if not set(repo.match(pattern)) & acknowledged:
            res.errors.append(
                f"`acknowledge: {pattern}` matches no dangling mention -- remove it"
            )
    return res


def closure(repo: SourceRepo, start: str) -> list[str]:
    """`start` and everything it requires, transitively, in resolution order."""
    seen, queue = [start], deque([start])
    while queue:
        for dep in repo.skills[queue.popleft()].requires:
            if dep not in seen:
                seen.append(dep)
                queue.append(dep)
    return seen


def _chain(res: Resolution, sid: str) -> str:
    links = [sid]
    while links[-1] in res.parent:
        links.append(res.parent[links[-1]])
    return " <- ".join(f"`{s}`" for s in links) + f" ({res.skills[links[-1]]})"


def _dangling(repo: SourceRepo, profile: Profile, res: Resolution) -> list[Dangling]:
    acknowledged = {sid for p in profile.acknowledge for sid in repo.match(p)}
    by_name: dict[str, list[str]] = defaultdict(list)
    for sid, s in repo.skills.items():
        if not s.is_shared:
            by_name[s.name].append(sid)
    out = []
    for sid in res.skills:
        skill = repo.skills[sid]
        for relpath in res.files[sid]:
            parts = relpath.split("/")
            if not relpath.endswith(".md") or any(
                w in parts[:-1] for w in EXEMPLAR_WELLS
            ):
                continue
            text = (
                (skill.path / relpath).read_text(encoding="utf-8").replace("\r\n", "\n")
            )
            for para in _PARAGRAPH.finditer(text):
                block = para.group(0)
                conditional = bool(_CONDITIONAL.search(block))
                for m in _MENTION.finditer(block):
                    slash, plugin, name = m.groups()
                    candidates = [
                        c
                        for c in by_name.get(name, [])
                        if not plugin or c.startswith(f"{plugin}:")
                    ]
                    if not candidates or any(c in res.skills for c in candidates):
                        continue
                    before = block[max(0, m.start() - 8) : m.start()]
                    after = block[m.end() : m.end() + 8]
                    if not (
                        slash
                        or plugin
                        or re.match(r"\s+skills?\b", after)
                        or re.search(r"\bskills?\s+$", before)
                    ):
                        continue
                    ref = candidates[0]
                    if ref in acknowledged:
                        status = "acknowledged"
                    elif ref in res.suggested:
                        status = "suggested"
                    elif conditional:
                        status = "conditional"
                    else:
                        status = "blocking"
                    line = text[: para.start() + m.start()].count("\n") + 1
                    out.append(Dangling(ref, f"{sid}/{relpath}:{line}", status))
    return out


def check(res: Resolution) -> None:
    """Raise when the resolution cannot be locked."""
    problems = list(res.errors)
    for d in res.blocking:
        problems.append(f"{d.at}: names `{d.ref}`, which does not ship")
    if problems:
        raise SkillshipError("cannot lock:\n" + "\n".join(f"  {p}" for p in problems))
