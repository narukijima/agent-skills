#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
status=0
skill_count=0

python3 "$repo_root/tools/check-runtime-permission-boundary.py"

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
version = re.search(r'^\s+claudagt\.version:\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', metadata_text, re.M)
status_value = re.search(r'^\s+claudagt\.status:\s*"(active|deprecated|retired)"\s*$', metadata_text, re.M)
aliases = re.search(r'^\s+claudagt\.aliases:\s*"([^"\n]+)"\s*$', metadata_text, re.M)
if not version:
    raise SystemExit("metadata.claudagt.version must be a quoted semver string")
if not status_value:
    raise SystemExit("metadata.claudagt.status must be active, deprecated, or retired")
if not aliases or not all(value.strip() for value in aliases.group(1).split(",")):
    raise SystemExit("metadata.claudagt.aliases must be a non-empty comma-separated string")
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
openai_path = skill_dir / "agents" / "openai.yaml"
if not openai_path.is_file():
    raise SystemExit("missing agents/openai.yaml")
openai_text = openai_path.read_text(encoding="utf-8")
display = re.search(r'^\s+display_name:\s*"([^"\n]+)"\s*$', openai_text, re.M)
short = re.search(r'^\s+short_description:\s*"([^"\n]+)"\s*$', openai_text, re.M)
prompt = re.search(r'^\s+default_prompt:\s*"([^"\n]+)"\s*$', openai_text, re.M)
implicit = re.search(r"^\s+allow_implicit_invocation:\s*(true|false)\s*$", openai_text, re.M)
if not display:
    raise SystemExit("agents/openai.yaml requires a quoted interface.display_name")
if not short or not 25 <= len(short.group(1)) <= 64:
    raise SystemExit("agents/openai.yaml short_description must be a quoted 25-64 character string")
if not prompt or ("$" + skill_dir.name) not in prompt.group(1):
    raise SystemExit("agents/openai.yaml default_prompt must reference $" + skill_dir.name)
if not implicit:
    raise SystemExit("agents/openai.yaml requires policy.allow_implicit_invocation: true or false")
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
