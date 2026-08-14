#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s <skill-name> --target <agent-directory-root>\n' "${0##*/}" >&2
}

if [[ $# -lt 3 ]]; then
  usage
  exit 2
fi

skill_name="$1"
shift
target_root=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      target_root="$2"
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$target_root" || "$skill_name" == */* || "$skill_name" == .* || "$skill_name" == _* ]]; then
  usage
  exit 2
fi

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
source_dir="$repo_root/skills/$skill_name"
destination="$target_root/skills/$skill_name"

[[ -f "$source_dir/SKILL.md" ]] || { printf 'ERROR: skill not found: %s\n' "$skill_name" >&2; exit 1; }
git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  printf 'ERROR: source repository is not a Git checkout\n' >&2
  exit 1
}
[[ ! -e "$destination" ]] || { printf 'ERROR: destination exists; refusing to overwrite: %s\n' "$destination" >&2; exit 1; }

if [[ -n "$(git -C "$repo_root" status --porcelain -- "skills/$skill_name")" ]]; then
  printf 'ERROR: source skill has uncommitted changes; provenance would not describe the copied bytes\n' >&2
  exit 1
fi
source_commit="$(git -C "$repo_root" rev-parse HEAD)"
source_repository="$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)"
if [[ -z "$source_repository" ]]; then
  source_repository='local checkout (origin is not configured)'
fi
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-skill-import.XXXXXX")"
trap 'rm -rf "$temporary_dir"' EXIT
source_version_file="$temporary_dir/source-version"
python3 - "$source_dir/SKILL.md" "$source_version_file" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
if not match:
    raise SystemExit("ERROR: source Skill has invalid frontmatter")

frontmatter = match.group(1).splitlines()
metadata_indent = None
version = None
legacy_version = None
description = None
for line in frontmatter:
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    indent = len(line) - len(line.lstrip(" "))
    if indent == 0:
        metadata_indent = 0 if line.strip() == "metadata:" else None
        description_match = re.fullmatch(r'description:\s*(.+?)\s*', line)
        if description_match:
            description = description_match.group(1).strip()
        legacy = re.fullmatch(r'version:\s*["\']?([^"\']+?)["\']?\s*', line)
        if legacy:
            legacy_version = legacy.group(1).strip()
        continue
    if metadata_indent is not None and indent > metadata_indent:
        item = re.fullmatch(r'\s*claudagt\.version:\s*["\']([^"\']+)["\']\s*', line)
        if item:
            version = item.group(1)
            break

if description is None or not 1 <= len(description) <= 200:
    raise SystemExit("ERROR: source Skill description is missing or longer than 200 characters")
Path(sys.argv[2]).write_text(version or legacy_version or "", encoding="utf-8")
PY
source_version="$(<"$source_version_file")"
[[ -n "$source_version" ]] || { printf 'ERROR: source Skill has no version\n' >&2; exit 1; }

mkdir -p "$target_root/skills"
cp -R "$source_dir" "$temporary_dir/$skill_name"
chmod -R u+w "$temporary_dir/$skill_name"
find "$temporary_dir/$skill_name" \( -type d -name '__pycache__' \) -prune -exec rm -rf {} +
find "$temporary_dir/$skill_name" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
python3 - "$temporary_dir/$skill_name/SKILL.md" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
if not match:
    raise SystemExit("ERROR: source Skill has invalid frontmatter")
frontmatter = match.group(1)
status = re.search(r'^\s+claudagt\.status:\s*"(active|deprecated|retired)"\s*$', frontmatter, re.M)
aliases = re.search(r'^\s+claudagt\.aliases:\s*"([^"]+)"\s*$', frontmatter, re.M)
if not status or not aliases:
    raise SystemExit("ERROR: source Skill has no agent-directory status / aliases metadata")
if re.search(r"^(?:status|aliases):", frontmatter, re.M):
    raise SystemExit("ERROR: source Skill unexpectedly contains legacy top-level status / aliases")
alias_values = [value.strip() for value in aliases.group(1).split(",") if value.strip()]
if not alias_values:
    raise SystemExit("ERROR: source Skill has empty agent-directory aliases metadata")
lines = frontmatter.splitlines()
insert_at = next((index for index, line in enumerate(lines) if line.startswith("metadata:")), len(lines))
projection = [
    "status: " + status.group(1),
    "aliases: [" + ", ".join(json.dumps(value, ensure_ascii=False) for value in alias_values) + "]",
]
lines[insert_at:insert_at] = projection
projected = "---\n" + "\n".join(lines) + "\n---\n" + text[match.end():]
path.write_text(projected, encoding="utf-8")
PY
escaped_repository="${source_repository//\\/\\\\}"
escaped_repository="${escaped_repository//\"/\\\"}"
mkdir -p "$temporary_dir/$skill_name/agents"
cat > "$temporary_dir/$skill_name/agents/upstream.yaml" <<EOF
source_repository: "$escaped_repository"
source_skill: "skills/$skill_name"
source_commit: "$source_commit"
source_version: "$source_version"
import_mode: "vendored-copy"
frontmatter_projection: "agent-directory-v1"
imported_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
mv "$temporary_dir/$skill_name" "$destination"
printf 'Imported %s to %s\n' "$skill_name" "$destination"
