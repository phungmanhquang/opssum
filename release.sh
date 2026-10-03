#!/usr/bin/env bash
# Release from a clean checkout. A failed push can be retried with ./release.sh.
set -Eeuo pipefail

trap 'code=$?; printf "\n[release] Lỗi ở dòng %s (exit %s). Không tự xoá commit/tag; chạy lại ./release.sh để tiếp tục nếu push thất bại.\n" "$LINENO" "$code" >&2' ERR

die() { printf '[release] Lỗi: %s\n' "$*" >&2; exit 1; }
say() { printf '[release] %s\n' "$*"; }

cd "$(dirname "$0")"
[[ "$(git rev-parse --show-toplevel 2>/dev/null)" == "$(pwd -P)" ]] || die "release.sh phải nằm ở gốc Git repository."
command -v python3 >/dev/null 2>&1 || die "Máy phát hành cần python3 để đọc/sửa pyproject.toml (người dùng binary không cần Python)."
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || die "Git working tree chưa sạch. Commit hoặc cất mọi thay đổi trước khi release."

branch="$(git symbolic-ref --quiet --short HEAD)" || die "Đang ở detached HEAD; hãy checkout một branch."
remote="${RELEASE_REMOTE:-origin}"
remote_url="$(git remote get-url "$remote")" || die "Không thấy Git remote '$remote'."
case "$remote_url" in
  https://github.com/*|git@github.com:*|ssh://git@github.com/*) ;;
  *) die "Remote '$remote' không trỏ tới github.com: $remote_url" ;;
esac
git var GIT_AUTHOR_IDENT >/dev/null || die "Chưa cấu hình Git author."
git var GIT_COMMITTER_IDENT >/dev/null || die "Chưa cấu hình Git committer."

current_version="$(python3 - <<'PY'
from pathlib import Path
import re
import sys

text = Path("pyproject.toml").read_text(encoding="utf-8")
project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group()) if project else None
if match is None or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", match.group(1)):
    sys.exit("[release] pyproject.toml cần [project].version dạng MAJOR.MINOR.PATCH.")
print(match.group(1))
PY
)" || die "Không đọc được version hiện tại."
say "Version hiện tại: $current_version (branch $branch, remote $remote)"

remote_tag_commit() {
  local listing sha ref
  listing="$(git ls-remote --tags "$remote" "refs/tags/$1" "refs/tags/$1^{}")" || die "Không đọc được tag từ $remote."
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
  listing="$(git ls-remote --heads "$remote" "refs/heads/$branch")" || die "Không đọc được branch từ $remote."
  printf '%s\n' "$listing" | awk -v ref="refs/heads/$branch" '$2 == ref {print $1; exit}'
}

push_release() {
  local tag="$1" head remote_tag remote_branch
  head="$(git rev-parse HEAD)"
  remote_tag="$(remote_tag_commit "$tag")"
  remote_branch="$(remote_branch_commit)"
  if [[ -n "$remote_tag" && "$remote_tag" != "$head" ]]; then
    die "Tag $tag trên GitHub đã trỏ tới commit khác; không ghi đè."
  fi
  if [[ "$remote_tag" == "$head" && "$remote_branch" == "$head" ]]; then
    say "$tag đã được push; không tạo release trùng. Kiểm tra GitHub Actions/Release."
    return
  fi
  if [[ "$remote_tag" == "$head" ]]; then
    say "Tag đã ở GitHub; push phần branch còn thiếu..."
    git push "$remote" "HEAD:refs/heads/$branch"
  else
    say "Push atomically commit và tag $tag lên $remote..."
    git push --atomic "$remote" "HEAD:refs/heads/$branch" "refs/tags/$tag:refs/tags/$tag"
  fi
  say "Đã push $tag. GitHub Actions sẽ build 5 binary và tạo Release khi mọi job thành công."
}

head_subject="$(git log -1 --format=%s)"
current_tag="v$current_version"
if [[ "$head_subject" == "chore(release): $current_tag" ]]; then
  existing_remote_tag="$(remote_tag_commit "$current_tag")" || die "Không kiểm tra được tag $current_tag trên GitHub."
  if [[ -n "$existing_remote_tag" && "$existing_remote_tag" != "$(git rev-parse HEAD)" ]]; then
    die "Tag $current_tag trên GitHub đã trỏ tới commit khác; không tự thay thế."
  fi
  local_tag="$(git rev-parse -q --verify "refs/tags/$current_tag^{commit}" 2>/dev/null || true)"
  if [[ -n "$local_tag" && "$local_tag" != "$(git rev-parse HEAD)" ]]; then
    die "Local tag $current_tag trỏ tới commit khác; không tự sửa tag."
  fi
  if [[ -z "$local_tag" ]]; then
    say "Tiếp tục release đã commit: tạo tag $current_tag còn thiếu."
    git tag -a "$current_tag" -m "Release $current_tag"
  else
    say "Tiếp tục/kiểm tra release $current_tag."
  fi
  push_release "$current_tag"
  exit 0
fi

bump="${1:-${RELEASE_BUMP:-}}"
if [[ -z "$bump" ]]; then
  IFS= read -r -p "Tăng version [patch/minor/major] (mặc định patch): " bump || die "Không nhận được lựa chọn tăng version."
  bump="${bump:-patch}"
fi
case "$bump" in
  patch|minor|major) ;;
  *) die "Chỉ chấp nhận patch, minor hoặc major." ;;
esac
IFS=. read -r major minor patch <<< "$current_version"
case "$bump" in
  patch) next_version="$major.$minor.$((patch + 1))" ;;
  minor) next_version="$major.$((minor + 1)).0" ;;
  major) next_version="$((major + 1)).0.0" ;;
esac
tag="v$next_version"
[[ -z "$(git rev-parse -q --verify "refs/tags/$tag^{commit}" 2>/dev/null || true)" ]] || die "Local tag $tag đã tồn tại."
new_remote_tag="$(remote_tag_commit "$tag")" || die "Không kiểm tra được tag $tag trên GitHub."
[[ -z "$new_remote_tag" ]] || die "Tag $tag đã tồn tại trên GitHub."
say "Chuẩn bị release $current_version → $next_version ($bump)"

python3 - "$current_version" "$next_version" <<'PY'
from pathlib import Path
import re
import sys

old, new = sys.argv[1:]
path = Path("pyproject.toml")
text = path.read_text(encoding="utf-8")
project = re.search(r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)", text)
if project is None:
    sys.exit("[release] Không thấy [project] trong pyproject.toml.")
section = project.group()
updated, count = re.subn(r'(?m)^(version\s*=\s*")' + re.escape(old) + r'("\s*)$',
                         lambda match: match.group(1) + new + match.group(2), section)
if count != 1:
    sys.exit("[release] Version đã đổi ngoài dự kiến; không ghi file.")
path.write_text(text[:project.start()] + updated + text[project.end():], encoding="utf-8")
PY

git add -- pyproject.toml
git commit -m "chore(release): $tag"
git tag -a "$tag" -m "Release $tag"
push_release "$tag"
