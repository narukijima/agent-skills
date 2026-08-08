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

if [[ -z "$target_root" || "$skill_name" == */* || "$skill_name" == .* ]]; then
  usage
  exit 2
fi

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
source_dir="$repo_root/skills/$skill_name"
destination="$target_root/skills/$skill_name"

[[ -f "$source_dir/SKILL.md" ]] || { printf 'ERROR: skill not found: %s\n' "$skill_name" >&2; exit 1; }
[[ -d "$repo_root/.git" ]] || { printf 'ERROR: source repository is not a Git checkout\n' >&2; exit 1; }
[[ ! -e "$destination" ]] || { printf 'ERROR: destination exists; refusing to overwrite: %s\n' "$destination" >&2; exit 1; }

source_commit="$(git -C "$repo_root" rev-parse HEAD)"
source_repository="$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)"
if [[ -z "$source_repository" ]]; then
  source_repository='local checkout (origin is not configured)'
fi
source_version="$(sed -n 's/^version:[[:space:]]*//p' "$source_dir/SKILL.md" | head -n 1)"
[[ -n "$source_version" ]] || { printf 'ERROR: source Skill has no version\n' >&2; exit 1; }

mkdir -p "$target_root/skills"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-skill-import.XXXXXX")"
trap 'rm -rf "$temporary_dir"' EXIT
cp -R "$source_dir" "$temporary_dir/$skill_name"
find "$temporary_dir/$skill_name" \( -type d -name '__pycache__' \) -prune -exec rm -rf {} +
find "$temporary_dir/$skill_name" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
mkdir -p "$temporary_dir/$skill_name/agents"
cat > "$temporary_dir/$skill_name/agents/upstream.yaml" <<EOF
source_repository: "$source_repository"
source_skill: "skills/$skill_name"
source_commit: "$source_commit"
source_version: "$source_version"
import_mode: "vendored-copy"
imported_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
mv "$temporary_dir/$skill_name" "$destination"
printf 'Imported %s to %s\n' "$skill_name" "$destination"
