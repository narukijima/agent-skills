#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s <skill-name> --target <consumer-root>\n' "${0##*/}" >&2
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

frontmatter = match.group(1)
description = re.search(r"^description:\s*(.+?)\s*$", frontmatter, re.M)
version = re.search(r'^\s+agent-directory\.version:\s*["\']([^"\']+)["\']\s*$', frontmatter, re.M)

if description is None or not 1 <= len(description.group(1)) <= 200:
    raise SystemExit("ERROR: source Skill description is missing or longer than 200 characters")
Path(sys.argv[2]).write_text(version.group(1) if version else "", encoding="utf-8")
PY
source_version="$(<"$source_version_file")"
[[ -n "$source_version" ]] || { printf 'ERROR: source Skill has no version\n' >&2; exit 1; }

mkdir -p "$target_root/skills"
archive_dir="$temporary_dir/archive"
mkdir -p "$archive_dir"
# Copy the exact tree named by source_commit. Ignored files (for example a local
# .env) are invisible to git status and must never enter a provenance-bound copy.
git -C "$repo_root" archive "$source_commit" -- "skills/$skill_name" | tar -x -C "$archive_dir"
mv "$archive_dir/skills/$skill_name" "$temporary_dir/$skill_name"
chmod -R u+w "$temporary_dir/$skill_name"
find "$temporary_dir/$skill_name" \( -type d -name '__pycache__' \) -prune -exec rm -rf {} +
find "$temporary_dir/$skill_name" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
python3 - "$temporary_dir/$skill_name/SKILL.md" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
if not match:
    raise SystemExit("ERROR: source Skill has invalid frontmatter")
frontmatter = match.group(1)
status = re.search(r'^\s+agent-directory\.status:\s*"(active|deprecated|retired)"\s*$', frontmatter, re.M)
aliases = re.search(r'^\s+agent-directory\.aliases:\s*"[^"\n]*"\s*$', frontmatter, re.M)
if not status or not aliases:
    raise SystemExit("ERROR: source Skill has no agent-directory status / aliases metadata")
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
imported_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
mv "$temporary_dir/$skill_name" "$destination"
printf 'Imported %s to %s\n' "$skill_name" "$destination"
