#!/bin/sh
# Install/update the latest standalone opssum binary (no Python needed).
set -eu

say() { printf '[opssum] %s\n' "$*"; }
fail() { printf '[opssum] Lỗi: %s\n' "$*" >&2; exit 1; }

# GitHub repository used for release assets. OPSSUM_REPO remains available for
# testing or a fork without requiring a source edit.
repo="${OPSSUM_REPO:-phungmanhquang/opssum}"
case "$repo" in
  OWNER/REPO) fail "GitHub owner/repo chưa được cấu hình hợp lệ." ;;
  */*) ;;
  *) fail "GitHub repo phải có dạng owner/repo." ;;
esac
case "$repo" in
  *[!A-Za-z0-9._/-]*|*/*/*|/*|*/|*..*) fail "GitHub owner/repo không hợp lệ: $repo" ;;
esac

command -v curl >/dev/null 2>&1 || fail "Cần curl để tải binary từ GitHub Releases."
command -v mktemp >/dev/null 2>&1 || fail "Cần mktemp để tải an toàn."

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) fail "OS chưa hỗ trợ bởi install.sh; Windows hãy dùng install.ps1." ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) fail "CPU chưa có binary phát hành: $(uname -m)." ;;
esac

asset="opssum-$os-$arch"
base="https://github.com/$repo/releases/latest/download"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/opssum-install.XXXXXXXX")" || fail "Không tạo được thư mục tạm."
trap 'if [ -n "${stage:-}" ] && [ -f "$stage" ]; then rm -f "$stage"; fi; rm -R "$temp_dir" 2>/dev/null || true' 0

say "Đang tải $asset từ GitHub Releases mới nhất..."
curl -fsSL --retry 3 --connect-timeout 10 --max-time 180 \
  "$base/$asset" -o "$temp_dir/$asset" || fail "Không tải được binary $asset. Kiểm tra repo/release và kết nối mạng."
curl -fsSL --retry 3 --connect-timeout 10 --max-time 60 \
  "$base/$asset.sha256" -o "$temp_dir/$asset.sha256" || fail "Không tải được checksum của $asset."

expected="$(awk -v name="$asset" '$2 == name {print $1; exit}' "$temp_dir/$asset.sha256")"
case "$expected" in
  ""|*[!0-9a-fA-F]*) fail "File SHA256 không hợp lệ cho $asset." ;;
esac
[ "${#expected}" -eq 64 ] || fail "File SHA256 không hợp lệ cho $asset."

if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$temp_dir/$asset" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  actual="$(shasum -a 256 "$temp_dir/$asset" | awk '{print $1}')"
elif command -v openssl >/dev/null 2>&1; then
  actual="$(openssl dgst -sha256 "$temp_dir/$asset" | awk '{print $NF}')"
else
  fail "Cần sha256sum, shasum hoặc openssl để xác minh binary."
fi
[ "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" = "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" ] || \
  fail "SHA256 không khớp; bản cài hiện tại được giữ nguyên."

chmod 755 "$temp_dir/$asset"
"$temp_dir/$asset" --version >/dev/null || fail "Binary không chạy trên máy này; bản cài hiện tại được giữ nguyên."

bin_dir="$HOME/.local/bin"
[ ! -L "$bin_dir" ] || fail "$bin_dir là symlink; không tự ghi qua đường dẫn này."
mkdir -p "$bin_dir"
destination="$bin_dir/opssum"
[ ! -d "$destination" ] || fail "$destination là thư mục, không thể ghi đè."

if [ -f "$destination" ] && [ ! -L "$destination" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    installed="$(sha256sum "$destination" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    installed="$(shasum -a 256 "$destination" | awk '{print $1}')"
  else
    installed=""
  fi
else
  installed=""
fi

if [ -n "$installed" ] && [ "$installed" = "$actual" ]; then
  say "$asset đã là bản mới nhất; không cần thay binary."
else
  stage="$bin_dir/.opssum-install.$$"
  [ ! -e "$stage" ] || fail "File tạm đã tồn tại: $stage"
  cp "$temp_dir/$asset" "$stage"
  chmod 755 "$stage"
  mv -f "$stage" "$destination" || fail "Không thể cập nhật $destination; bản cũ được giữ nguyên."
  stage=
  say "Đã cài/cập nhật: $destination"
fi

case ":${PATH:-}:" in
  *":$bin_dir:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh) profile="$HOME/.zshrc" ;;
      */bash) profile="$HOME/.bashrc" ;;
      *) profile="$HOME/.profile" ;;
    esac
    say "$bin_dir chưa nằm trong PATH. Thêm dòng này vào $profile rồi mở terminal mới:"
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    ;;
esac
say "Chạy: opssum init --examples  (lần đầu), sau đó opssum"
