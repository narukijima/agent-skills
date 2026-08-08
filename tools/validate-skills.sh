#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
status=0
skill_count=0

while IFS= read -r skill_file; do
  skill_count=$((skill_count + 1))
  skill_dir="$(dirname -- "$skill_file")"
  skill_name="$(basename -- "$skill_dir")"
  if [[ "$skill_name" == _template ]]; then
    skill_count=$((skill_count - 1))
    continue
  fi
  if [[ -e "$skill_dir/README.md" ]]; then
    printf 'FAIL: skill must not contain README.md: %s\n' "$skill_dir" >&2
    status=1
  fi
  if ! python3 - "$skill_dir" <<'PY'
import pathlib
import re
import sys

skill_dir = pathlib.Path(sys.argv[1])
skill_file = skill_dir / "SKILL.md"
text = skill_file.read_text(encoding="utf-8")
if not text.startswith("---\n"):
    raise SystemExit("missing frontmatter")
match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
if not match:
    raise SystemExit("invalid frontmatter")
frontmatter = match.group(1)
name = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.M)
description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
status_value = re.search(r"^status:\s*(active|deprecated|retired)$", frontmatter, re.M)
aliases = re.search(r"^aliases:\s*(.+)$", frontmatter, re.M)
version = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)$", frontmatter, re.M)
if not name or name.group(1).strip() != skill_dir.name:
    raise SystemExit("frontmatter name does not match directory")
if not description or len(description.group(1).strip()) > 200:
    raise SystemExit("description is missing or longer than 200 characters")
if not status_value:
    raise SystemExit("status must be active, deprecated, or retired")
if not aliases or not aliases.group(1).strip():
    raise SystemExit("aliases is missing")
if not version:
    raise SystemExit("version must use semver")
if len(text.encode("utf-8")) > 20 * 1024:
    raise SystemExit("SKILL.md is larger than 20 KiB")
for heading in ("## 使用するKnowledge", "### Required", "### Conditional"):
    if heading not in text:
        raise SystemExit("missing required heading: " + heading)
PY
  then
    printf 'FAIL: invalid skill contract: %s\n' "$skill_dir" >&2
    status=1
  fi
done < <(find "$repo_root/skills" -mindepth 2 -maxdepth 2 -type f -name SKILL.md -print | sort)

if [[ "$skill_count" -eq 0 ]]; then
  printf 'FAIL: no skills found\n' >&2
  exit 1
fi
if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi
printf 'PASS: %s skill(s) validated\n' "$skill_count"
