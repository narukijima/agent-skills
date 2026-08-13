#!/usr/bin/env python3
"""Fail closed on public-repository PII and credential-shaped content."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NOREPLY = re.compile(r"(?:noreply@github\.com|[0-9]+\+[A-Za-z0-9-]+@users\.noreply\.github\.com)$", re.I)
ALLOWED_FIXTURE_EMAIL = re.compile(rb"@example\.(?:com|invalid|test)$", re.I)
LOCAL_PATH = re.compile(
    rb"(?:/" + rb"Users/(?!me/)[^\s\"']+|/" + rb"home/(?!runner/)[^\s\"']+|[A-Za-z]:\\" + rb"Users\\[^\s\"']+)"
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(rb"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{20,}\b", re.I),
)


def git(*args: str) -> bytes:
    return subprocess.run(("git", *args), check=True, stdout=subprocess.PIPE).stdout


def tracked_files() -> list[Path]:
    return [Path(item.decode("utf-8")) for item in git("ls-files", "-z").split(b"\0") if item]


def scan_bytes(path: Path, content: bytes) -> list[str]:
    findings: list[str] = []
    for match in EMAIL.finditer(content):
        if not ALLOWED_FIXTURE_EMAIL.search(match.group(0)):
            findings.append(f"{path}: non-fixture email")
    if LOCAL_PATH.search(content):
        findings.append(f"{path}: personal local path")
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            findings.append(f"{path}: credential-shaped content")
            break
    return findings


def scan_worktree() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        if path.is_file():
            findings.extend(scan_bytes(path, path.read_bytes()))
    return findings


def scan_commits(revision_range: str) -> list[str]:
    if not revision_range or revision_range.startswith("0000000000000000000000000000000000000000.."):
        revision_range = "HEAD"
    rows = git("log", revision_range, "--format=%H%x00%ae%x00%ce%x00").split(b"\0")
    findings: list[str] = []
    for index in range(0, len(rows) - 2, 3):
        commit = rows[index].decode("ascii", "replace")[:12]
        for role, raw in (("author", rows[index + 1]), ("committer", rows[index + 2])):
            value = raw.decode("utf-8", "replace")
            if value and not NOREPLY.fullmatch(value):
                findings.append(f"{commit}: {role} email is not GitHub noreply")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-range", default="HEAD")
    args = parser.parse_args()
    findings = scan_worktree() + scan_commits(args.commit_range)
    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1
    print("PUBLIC_REPO_SCAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
