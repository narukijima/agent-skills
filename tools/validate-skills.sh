#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
status=0
skill_count=0

python3 "$repo_root/tools/public_repo_scan.py" --commit-range HEAD

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
lines = frontmatter.splitlines()
top_level_keys = {
    item.group(1)
    for line in lines
    if (item := re.match(r"^([a-z][a-z0-9-]*):(?:\s|$)", line))
}
allowed_keys = {"name", "description", "license", "compatibility", "metadata"}
unknown_keys = sorted(top_level_keys - allowed_keys)
if unknown_keys:
    raise SystemExit("unsupported top-level frontmatter keys: " + ", ".join(unknown_keys))
name = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.M)
description = re.search(r"^description:\s*(.+)$", frontmatter, re.M)
license_value = re.search(r"^license:\s*(.+)$", frontmatter, re.M)
metadata = re.search(r"^metadata:\s*$\n((?:^[ ]+.+$\n?)*)", frontmatter, re.M)
if not name or name.group(1).strip() != skill_dir.name:
    raise SystemExit("frontmatter name does not match directory")
if not description or not 1 <= len(description.group(1).strip()) <= 200:
    raise SystemExit("description is missing or longer than 200 characters")
if not license_value:
    raise SystemExit("license is required by this repository")
if "LICENSE.txt" in license_value.group(1) and not (skill_dir / "LICENSE.txt").is_file():
    raise SystemExit("frontmatter references missing LICENSE.txt")
if not metadata:
    raise SystemExit("metadata is required by this repository")
metadata_text = metadata.group(1)
version = re.search(r'^\s+agent-directory\.version:\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', metadata_text, re.M)
status_value = re.search(r'^\s+agent-directory\.status:\s*"(active|deprecated|retired)"\s*$', metadata_text, re.M)
aliases = re.search(r'^\s+agent-directory\.aliases:\s*"([^"\n]*)"\s*$', metadata_text, re.M)
if not version:
    raise SystemExit("metadata.agent-directory.version must be a quoted semver string")
if not status_value:
    raise SystemExit("metadata.agent-directory.status must be active, deprecated, or retired")
if aliases is None:
    raise SystemExit("metadata.agent-directory.aliases must be a quoted comma-separated string")
if len(text.encode("utf-8")) > 20 * 1024:
    raise SystemExit("SKILL.md is larger than 20 KiB")
for heading in ("## 使用するKnowledge", "### Required", "### Conditional"):
    if heading not in text:
        raise SystemExit("missing required heading: " + heading)
knowledge_section = re.search(
    r"^## 使用するKnowledge\s*$\n(.*?)(?=^## |\Z)", text, re.M | re.S
)
required_section = re.search(
    r"^### Required\s*$\n(.*?)(?=^### |\Z)", knowledge_section.group(1), re.M | re.S
)
required_count = sum(
    line.startswith("- ") and line.strip() != "- なし"
    for line in required_section.group(1).splitlines()
)
if required_count > 3:
    raise SystemExit("SKILL.md has more than 3 Required Knowledge references")
catalog_path = skill_dir.parent / "SKILLS.md"
if not catalog_path.is_file():
    raise SystemExit("missing skills/SKILLS.md catalog")
if "[`" + skill_dir.name + "`](" + skill_dir.name + "/SKILL.md)" not in catalog_path.read_text(encoding="utf-8"):
    raise SystemExit("skill is not registered in skills/SKILLS.md")
references_dir = skill_dir / "references"
if references_dir.is_dir():
    for reference in sorted(references_dir.rglob("*.md")):
        relative = reference.relative_to(skill_dir).as_posix()
        if relative not in text:
            raise SystemExit("orphan reference is not linked from SKILL.md: " + relative)
for linked in re.finditer(r"`(references/[^`\n]+\.md)`", text):
    if not (skill_dir / linked.group(1)).is_file():
        raise SystemExit("SKILL.md references a missing file: " + linked.group(1))
PY
  then
    printf 'FAIL: invalid skill contract: %s\n' "$skill_dir" >&2
    status=1
  fi
  if [[ -d "$skill_dir/scripts" ]] && ! python3 -m compileall -q "$skill_dir/scripts" >/dev/null 2>&1; then
    printf 'FAIL: skill scripts do not compile: %s\n' "$skill_dir" >&2
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
