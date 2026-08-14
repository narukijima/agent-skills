#!/usr/bin/env python3
"""Reject provider-runtime permission policy from reusable Skill surfaces."""

from __future__ import annotations

import re
from pathlib import Path


FORBIDDEN = {
    "claude-permission-mode": re.compile(r"--permission" + r"-mode\b"),
    "claude-permission-bypass": re.compile(r"--dangerously" + r"-skip-permissions\b|bypass" + r"Permissions\b"),
    "claude-edit-mode": re.compile(r"accept" + r"Edits\b"),
    "codex-sandbox-mode": re.compile(r"\bworkspace" + r"-write\b|--sand" + r"box\b"),
    "codex-approval-mode": re.compile(r"--approval" + r"-policy\b|--ask-for" + r"-approval\b"),
    "provider-full-auto": re.compile(r"--full" + r"-auto\b|\bFull" + r" Auto\b"),
    "generic-tool-permission-frontmatter": re.compile(r"^allowed" + r"-tools\s*:", re.M),
    "generic-shell-approval": re.compile(
        r"shell実行前に必ず" + r"ユーザー承認|ask (?:the )?user before (?:running|executing) (?:the )?shell",
        re.I,
    ),
    "generic-filesystem-network-approval": re.compile(
        r"(?:generic )?(?:filesystem|network) approval|(?:filesystem|network)アクセス前に必ず(?:ユーザー)?承認",
        re.I,
    ),
}


def scan_text(path: Path, text: str) -> list[str]:
    return [f"{path}: {label}" for label, pattern in FORBIDDEN.items() if pattern.search(text)]


def candidate_files(root: Path) -> list[Path]:
    files = [root / "README.md", root / "AGENTS.md", root / "PROJECT.md", root / "STATE.md"]
    for directory in ("skills", "evals", "tools", "tests", ".github"):
        base = root / directory
        if base.is_dir():
            files.extend(path for path in base.rglob("*") if path.is_file())
    excluded = {"LICENSE.txt", "test_runtime_permission_boundary.py"}
    return sorted({path for path in files if path.is_file() and path.name not in excluded})


def scan_repository(root: Path) -> list[str]:
    findings: list[str] = []
    for path in candidate_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(path.relative_to(root), text))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = scan_repository(root)
    if findings:
        for finding in findings:
            print("FAIL: " + finding)
        return 1
    print("PASS: no provider-runtime permission policy in Skill surfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
