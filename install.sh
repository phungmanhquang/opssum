#!/bin/sh
# Install/update the latest standalone opssum binary (no Python needed).
set -eu

say() { printf '[opssum] %s\n' "$*"; }
fail() { printf '[opssum] Error: %s\n' "$*" >&2; exit 1; }

# GitHub repository used for release assets. OPSSUM_REPO remains available for
# testing or a fork without requiring a source edit.
repo="${OPSSUM_REPO:-phungmanhquang/opssum}"
case "$repo" in
  OWNER/REPO) fail "GitHub owner/repo is not configured correctly." ;;
  */*) ;;
  *) fail "GitHub repository must use the owner/repo format." ;;
esac
case "$repo" in
  *[!A-Za-z0-9._/-]*|*/*/*|/*|*/|*..*) fail "Invalid GitHub owner/repo: $repo" ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required to download a binary from GitHub Releases."
command -v mktemp >/dev/null 2>&1 || fail "mktemp is required for a safe download."

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) fail "install.sh does not support this OS; Windows users should use install.ps1." ;;
esac
case "$(uname -m)" in
  x86_64|amd64) arch=x64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) fail "No release binary is available for CPU: $(uname -m)." ;;
esac

asset="opssum-$os-$arch"
base="https://github.com/$repo/releases/latest/download"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/opssum-install.XXXXXXXX")" || fail "Could not create a temporary directory."
trap 'if [ -n "${stage:-}" ] && [ -f "$stage" ]; then rm -f "$stage"; fi; rm -R "$temp_dir" 2>/dev/null || true' 0

say "Downloading $asset from the latest GitHub Release..."
curl -fsSL --retry 3 --connect-timeout 10 --max-time 180 \
  "$base/$asset" -o "$temp_dir/$asset" || fail "Could not download binary $asset. Check the repository, release, and network connection."
curl -fsSL --retry 3 --connect-timeout 10 --max-time 60 \
  "$base/$asset.sha256" -o "$temp_dir/$asset.sha256" || fail "Could not download the checksum for $asset."

expected="$(awk -v name="$asset" '$2 == name {print $1; exit}' "$temp_dir/$asset.sha256")"
case "$expected" in
  ""|*[!0-9a-fA-F]*) fail "Invalid SHA256 file for $asset." ;;
esac
[ "${#expected}" -eq 64 ] || fail "Invalid SHA256 file for $asset."

if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$temp_dir/$asset" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  actual="$(shasum -a 256 "$temp_dir/$asset" | awk '{print $1}')"
elif command -v openssl >/dev/null 2>&1; then
  actual="$(openssl dgst -sha256 "$temp_dir/$asset" | awk '{print $NF}')"
else
  fail "sha256sum, shasum, or openssl is required to verify the binary."
fi
[ "$(printf '%s' "$actual" | tr 'A-F' 'a-f')" = "$(printf '%s' "$expected" | tr 'A-F' 'a-f')" ] || \
  fail "SHA256 mismatch; the current installation was kept unchanged."

chmod 755 "$temp_dir/$asset"
"$temp_dir/$asset" --version >/dev/null || fail "The binary cannot run on this machine; the current installation was kept unchanged."

bin_dir="$HOME/.local/bin"
[ ! -L "$bin_dir" ] || fail "$bin_dir is a symlink; refusing to write through it."
mkdir -p "$bin_dir"
destination="$bin_dir/opssum"
[ ! -d "$destination" ] || fail "$destination is a directory and cannot be overwritten."

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
  say "$asset is already up to date; no binary replacement is needed."
else
  stage="$bin_dir/.opssum-install.$$"
  [ ! -e "$stage" ] || fail "Temporary file already exists: $stage"
  cp "$temp_dir/$asset" "$stage"
  chmod 755 "$stage"
  mv -f "$stage" "$destination" || fail "Could not update $destination; the previous binary was kept."
  stage=
  say "Installed/updated: $destination"
fi

case ":${PATH:-}:" in
  *":$bin_dir:"*) ;;
  *)
    case "${SHELL:-}" in
      */zsh) profile="$HOME/.zshrc" ;;
      */bash) profile="$HOME/.bashrc" ;;
      *) profile="$HOME/.profile" ;;
    esac
    say "$bin_dir is not on PATH. Add this line to $profile, then open a new terminal:"
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    ;;
esac
say "Run: opssum init --examples (first time), then opssum"
