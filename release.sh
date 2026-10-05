#!/usr/bin/env bash
# Release from a clean checkout. A failed push can be retried with ./release.sh.
set -Eeuo pipefail

trap 'code=$?; printf "\n[release] Error on line %s (exit %s). The commit/tag was not removed; rerun ./release.sh to continue after a failed push.\n" "$LINENO" "$code" >&2' ERR

die() { printf '[release] Error: %s\n' "$*" >&2; exit 1; }
say() { printf '[release] %s\n' "$*"; }

cd "$(dirname "$0")"
[[ "$(git rev-parse --show-toplevel 2>/dev/null)" == "$(pwd -P)" ]] || die "release.sh must be at the Git repository root."
command -v python3 >/dev/null 2>&1 || die "The release machine needs python3 to read/update pyproject.toml (binary users do not need Python)."
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || die "The Git working tree is not clean. Commit or stash all changes before releasing."

branch="$(git symbolic-ref --quiet --short HEAD)" || die "HEAD is detached; checkout a branch first."
remote="${RELEASE_REMOTE:-origin}"
remote_url="$(git remote get-url "$remote")" || die "Git remote '$remote' was not found."
case "$remote_url" in
  https://github.com/*|git@github.com:*|ssh://git@github.com/*) ;;
  *) die "Remote '$remote' does not point to github.com: $remote_url" ;;
esac
git var GIT_AUTHOR_IDENT >/dev/null || die "Git author is not configured."
git var GIT_COMMITTER_IDENT >/dev/null || die "Git committer is not configured."

current_version="$(python3 - <<'PY'
from pathlib import Path
import re
import sys

text = Path("pyproject.toml").read_text(encoding="utf-8")
project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group()) if project else None
if match is None or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", match.group(1)):
    sys.exit("[release] pyproject.toml must define [project].version as MAJOR.MINOR.PATCH.")
print(match.group(1))
PY
)" || die "Could not read the current version."
say "Current version: $current_version (branch $branch, remote $remote)"

remote_tag_commit() {
  local listing sha ref
  listing="$(git ls-remote --tags "$remote" "refs/tags/$1" "refs/tags/$1^{}")" || die "Could not read the tag from $remote."
  ref="refs/tags/$1^{}"
  sha="$(printf '%s\n' "$listing" | awk -v ref="$ref" '$2 == ref {print $1; exit}')"
  if [[ -z "$sha" ]]; then
    ref="refs/tags/$1"
    sha="$(printf '%s\n' "$listing" | awk -v ref="$ref" '$2 == ref {print $1; exit}')"
  fi
  printf '%s' "$sha"
}

remote_branch_commit() {
  local listing
  listing="$(git ls-remote --heads "$remote" "refs/heads/$branch")" || die "Could not read the branch from $remote."
  printf '%s\n' "$listing" | awk -v ref="refs/heads/$branch" '$2 == ref {print $1; exit}'
}

push_release() {
  local tag="$1" head remote_tag remote_branch
  head="$(git rev-parse HEAD)"
  remote_tag="$(remote_tag_commit "$tag")"
  remote_branch="$(remote_branch_commit)"
  if [[ -n "$remote_tag" && "$remote_tag" != "$head" ]]; then
    die "Tag $tag on GitHub points to a different commit; refusing to overwrite it."
  fi
  if [[ "$remote_tag" == "$head" && "$remote_branch" == "$head" ]]; then
    say "$tag has already been pushed; no duplicate release will be created. Check GitHub Actions/Release."
    return
  fi
  if [[ "$remote_tag" == "$head" ]]; then
    say "The tag is already on GitHub; pushing the missing branch commit..."
    git push "$remote" "HEAD:refs/heads/$branch"
  else
    say "Atomically pushing the commit and tag $tag to $remote..."
    git push --atomic "$remote" "HEAD:refs/heads/$branch" "refs/tags/$tag:refs/tags/$tag"
  fi
  say "Pushed $tag. GitHub Actions will build five binaries and create a Release when every job succeeds."
}

head_subject="$(git log -1 --format=%s)"
current_tag="v$current_version"
if [[ "$head_subject" == "chore(release): $current_tag" ]]; then
  existing_remote_tag="$(remote_tag_commit "$current_tag")" || die "Could not check tag $current_tag on GitHub."
  if [[ -n "$existing_remote_tag" && "$existing_remote_tag" != "$(git rev-parse HEAD)" ]]; then
    die "Tag $current_tag on GitHub points to a different commit; refusing to replace it."
  fi
  local_tag="$(git rev-parse -q --verify "refs/tags/$current_tag^{commit}" 2>/dev/null || true)"
  if [[ -n "$local_tag" && "$local_tag" != "$(git rev-parse HEAD)" ]]; then
    die "Local tag $current_tag points to a different commit; refusing to rewrite it."
  fi
  if [[ -z "$local_tag" ]]; then
    say "Continuing the committed release: creating the missing tag $current_tag."
    git tag -a "$current_tag" -m "Release $current_tag"
  else
    say "Continuing/checking release $current_tag."
  fi
  push_release "$current_tag"
  exit 0
fi

bump="${1:-${RELEASE_BUMP:-}}"
if [[ -z "$bump" ]]; then
  IFS= read -r -p "Version bump [patch/minor/major] (default: patch): " bump || die "Did not receive a version choice."
  bump="${bump:-patch}"
fi
case "$bump" in
  patch|minor|major) ;;
  *) die "Only patch, minor, or major bumps are accepted." ;;
esac
IFS=. read -r major minor patch <<< "$current_version"
case "$bump" in
  patch) next_version="$major.$minor.$((patch + 1))" ;;
  minor) next_version="$major.$((minor + 1)).0" ;;
  major) next_version="$((major + 1)).0.0" ;;
esac
tag="v$next_version"
[[ -z "$(git rev-parse -q --verify "refs/tags/$tag^{commit}" 2>/dev/null || true)" ]] || die "Local tag $tag already exists."
new_remote_tag="$(remote_tag_commit "$tag")" || die "Could not check tag $tag on GitHub."
[[ -z "$new_remote_tag" ]] || die "Tag $tag already exists on GitHub."
say "Preparing release $current_version → $next_version ($bump)"

python3 - "$current_version" "$next_version" <<'PY'
from pathlib import Path
import re
import sys

old, new = sys.argv[1:]
path = Path("pyproject.toml")
text = path.read_text(encoding="utf-8")
project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
if project is None:
    sys.exit("[release] [project] was not found in pyproject.toml.")
section = project.group()
updated, count = re.subn(r'(?m)^(version\s*=\s*")' + re.escape(old) + r'("\s*)$',
                         lambda match: match.group(1) + new + match.group(2), section)
if count != 1:
    sys.exit("[release] The version changed unexpectedly; refusing to write the file.")
path.write_text(text[:project.start()] + updated + text[project.end():], encoding="utf-8")
PY

git add -- pyproject.toml
git commit -m "chore(release): $tag"
git tag -a "$tag" -m "Release $tag"
push_release "$tag"
