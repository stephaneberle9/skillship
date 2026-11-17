"""A pre-flight of Agensi's security scan, run on the package `agensi` builds.

Agensi scans every submission with eight checks, each ending as pass, warn or
fail, and scores it 100 minus 20 per failed check and 5 per warning. Its
documentation names what fails each check and which patterns only warn; this
module looks for the same patterns, so a finding shows up before the
submission rather than in Agensi's review. It is a heuristic, like the scan:
a failed check here is something to look at, and a false positive there does
not block approval.

Two notes are not scored: a script that reads files next to it, since Agensi
runs each script alone from a temporary file, and a script over 20 KB, the size
Agensi asks script logic to stay under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from skillship.upload import BUNDLED, EXECUTABLES, Contents, frontmatter

CODE = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".psm1",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".rb",
    ".pl",
)
SCRIPT_LIMIT = 20 * 1024

_DANGEROUS = [
    (re.compile(r"\brm\s+-[A-Za-z]*\s+/(?:\*|\s|$)"), "deletes from the root path"),
    (
        re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
        "pipes a download into a shell",
    ),
    (
        re.compile(
            r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*\|\s*iex\b",
            re.IGNORECASE,
        ),
        "pipes a download into a shell",
    ),
    (re.compile(r"\bos\.system\s*\("), "calls os.system"),
    (re.compile(r"\bshell\s*=\s*True\b"), "runs a subprocess with shell=True"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b"), "sets mode 777"),
]
_WARNS = [
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bwget\b"), "wget"),
    (re.compile(r"\bfetch\s*\("), "fetch"),
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bbase64\b", re.IGNORECASE), "base64"),
]
_WRITES = re.compile(
    r"open\([^)\n]*['\"][wax]b?\+?['\"]|\.write_(?:text|bytes)\(|writeFileSync|"
    r"\bwriteFile\(|\bSet-Content\b|\bOut-File\b|\bAdd-Content\b"
)
_SECRETS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"), "a GitHub token"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20}\b"), "a GitLab token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key id"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "an Anthropic API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"), "an API key"),
    (re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"), "a private key"),
]
_ENV = re.compile(
    r"\bos\.environ\b|\bgetenv\s*\(|\bprocess\.env\b|\$env:|\bENV\[|\bprintenv\b"
)
_NETWORK = re.compile(
    r"\brequests\.|\burllib\b|\bhttp\.client\b|\bhttpx\b|\baiohttp\b|\bsmtplib\b|"
    r"\bsocket\.|\bfetch\s*\(|\baxios\b|\bcurl\b|\bwget\b|\bInvoke-(?:WebRequest|RestMethod)\b"
)
_LOCAL_READ = re.compile(
    r"\bopen\s*\(|\.read_(?:text|bytes)\(|readFileSync|\breadFile\(|\bGet-Content\b|\bcat\s+[^|\n]"
)
_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_HEX = re.compile(r"(?:\\x[0-9A-Fa-f]{2}){10,}")
_EVAL = re.compile(r"\b(?:eval|exec)\s*\(")
_DECODE = re.compile(
    r"b64decode|\batob\s*\(|fromCharCode|codecs\.decode|bytes\.fromhex"
)
_INJECTION = re.compile(
    r"ignore (?:all )?(?:previous|prior|above) instructions|do not tell the user|"
    r"don't tell the user|\bsecretly\b",
    re.IGNORECASE,
)
_SIBLING = re.compile(
    r"__file__|\$\(dirname\s+\"?\$0|\$\{BASH_SOURCE|\$PSScriptRoot|__dirname|import\.meta\.url"
)


@dataclass
class Check:
    name: str
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "fail" if self.fails else "warn" if self.warns else "pass"


@dataclass
class Report:
    checks: list[Check]
    notes: list[str]

    @property
    def score(self) -> int:
        fails = sum(c.status == "fail" for c in self.checks)
        warns = sum(c.status == "warn" for c in self.checks)
        return max(0, 100 - 20 * fails - 5 * warns)


def scan(files: Contents) -> Report:
    checks = {
        n: Check(n)
        for n in (
            "File structure",
            "File types",
            "Dangerous commands",
            "Secrets",
            "Environment harvesting",
            "Network access",
            "Obfuscation",
            "Prompt injection",
        )
    }
    notes = []
    skill_mds = sorted(p for p in files if PurePosixPath(p).name == "SKILL.md")
    if not skill_mds:
        checks["File structure"].fails.append("no SKILL.md")
    for p in skill_mds:
        meta = frontmatter(files[p][0].decode("utf-8", errors="replace"))
        if not meta:
            checks["File structure"].fails.append(f"{p}: no frontmatter")
        elif not meta.get("description"):
            checks["File structure"].fails.append(f"{p}: no `description`")
        elif not meta.get("name") and p.count("/") <= 2:
            checks["File structure"].fails.append(f"{p}: no `name`")

    for path, (data, _) in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if path.lower().endswith(EXECUTABLES):
            checks["File types"].fails.append(f"{path}: an executable")
        if any(b in parts for b in BUNDLED):
            checks["File types"].fails.append(f"{path}: a bundled dependency")
        text = _text(data)
        if text is None:
            checks["File types"].warns.append(
                f"{path}: not text, which agents cannot read"
            )
            continue
        code = path.lower().endswith(CODE)

        def at(m: re.Match[str], text: str = text, path: str = path) -> str:
            return f"{path}:{text.count(chr(10), 0, m.start()) + 1}"

        for rx, what in _DANGEROUS:
            for m in rx.finditer(text):
                checks["Dangerous commands"].fails.append(f"{at(m)}: {what}")
        for rx, what in _WARNS:
            m = rx.search(text)
            if m:
                checks["Dangerous commands"].warns.append(f"{at(m)}: {what}")
        if code:
            m = _WRITES.search(text)
            if m:
                checks["Dangerous commands"].warns.append(f"{at(m)}: writes a file")
        for rx, what in _SECRETS:
            for m in rx.finditer(text):
                checks["Secrets"].fails.append(f"{at(m)}: {what}")
        if code:
            env, net, read = (
                _ENV.search(text),
                _NETWORK.search(text),
                _LOCAL_READ.search(text),
            )
            if env and net:
                checks["Environment harvesting"].fails.append(
                    f"{at(env)}: reads environment variables, and {at(net)} reaches the network"
                )
            if read and net:
                checks["Network access"].fails.append(
                    f"{at(read)}: reads local data, and {at(net)} reaches the network"
                )
            if _EVAL.search(text) and _DECODE.search(text):
                m = _EVAL.search(text)
                assert m
                checks["Obfuscation"].fails.append(f"{at(m)}: evaluates decoded code")
            m = _SIBLING.search(text)
            if m:
                notes.append(
                    f"{at(m)}: reads files next to the script; Agensi runs each script "
                    "alone, so embed what it needs"
                )
            if len(data) > SCRIPT_LIMIT:
                notes.append(
                    f"{path}: {len(data)} bytes; Agensi asks for script logic under 20 KB"
                )
        for rx in (_BLOB, _HEX):
            for m in rx.finditer(text):
                checks["Obfuscation"].fails.append(f"{at(m)}: an encoded blob")
        for m in _INJECTION.finditer(text):
            checks["Prompt injection"].fails.append(f"{at(m)}: “{m.group(0)}”")
    return Report(list(checks.values()), notes)


def _text(data: bytes) -> str | None:
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def render(report: Report, name: str) -> str:
    """The report as Markdown."""
    lines = [
        f"# Agensi pre-flight: {name}",
        "",
        f"Score: **{report.score}** (100, minus 20 per failed check and 5 per warning).",
        "A heuristic of Agensi's scan: a finding is something to look at, not a verdict.",
        "",
        "| Check | Result |",
        "| --- | --- |",
        *(f"| {c.name} | {c.status} |" for c in report.checks),
        "",
    ]
    for c in report.checks:
        if c.fails or c.warns:
            lines += [f"## {c.name}", ""]
            lines += [f"- fail: {f}" for f in c.fails]
            lines += [f"- warn: {w}" for w in c.warns]
            lines.append("")
    if report.notes:
        lines += ["## Scripts", "", *(f"- {n}" for n in report.notes), ""]
    return "\n".join(lines)
