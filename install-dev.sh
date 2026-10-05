#!/bin/sh
# Install the current checkout in editable mode for local development.
# This is intentionally separate from install.sh: it never downloads a release
# binary and it installs the command as `opssum-dev`.
set -eu

say() { printf '[opssum-dev] %s\n' "$*"; }
fail() { printf '[opssum-dev] Error: %s\n' "$*" >&2; exit 1; }

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
python_cmd="${PYTHON:-python3}"
venv="${OPSSUM_DEV_VENV:-$HOME/.local/share/opssum-dev/venv}"
bin_dir="${OPSSUM_DEV_BIN_DIR:-$HOME/.local/bin}"
command_name="opssum-dev"
launcher="$bin_dir/$command_name"

if [ "${1:-}" = "--uninstall" ]; then
  if [ -L "$launcher" ] && [ "$(readlink "$launcher")" = "$venv/bin/opssum" ]; then
    rm -f "$launcher"
    say "Removed command $launcher"
  elif [ -e "$launcher" ]; then
    fail "$launcher was not created by this dev installer; leaving it unchanged."
  fi
  if [ -L "$venv" ]; then
    fail "$venv is a symlink; refusing to remove an external target."
  fi
  if [ -d "$venv" ]; then
    rm -rf "$venv"
    say "Removed virtualenv $venv"
  fi
  exit 0
fi
[ "${1:-}" = "" ] || fail "Invalid argument: $1 (use --uninstall to remove the dev build)"

command -v "$python_cmd" >/dev/null 2>&1 || fail "$python_cmd was not found. Python 3.10 or newer is required."
version="$($python_cmd -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
major=${version%%.*}
minor=${version#*.}
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
  fail "Python 3.10 or newer is required; found Python $version."
fi

if [ -e "$venv" ] && [ ! -x "$venv/bin/python" ]; then
  fail "$venv is not a valid Python virtualenv; remove it or set OPSSUM_DEV_VENV to another path."
fi
if [ ! -e "$venv" ]; then
  say "Creating the dev virtualenv at $venv..."
  mkdir -p "$(dirname -- "$venv")"
  "$python_cmd" -m venv "$venv" || fail "Could not create the virtualenv. You may need to install python3-venv."
fi

dev_python="$venv/bin/python"
[ -x "$dev_python" ] || fail "The virtualenv does not contain $dev_python."
"$dev_python" -m pip --version >/dev/null 2>&1 || fail "The virtualenv has no pip; reinstall Python/venv and try again."

say "Installing the editable package and dev dependencies from the current checkout..."
"$dev_python" -m pip install --editable "$root[dev]"

mkdir -p "$bin_dir"
if [ -L "$launcher" ] && [ "$(readlink "$launcher")" != "$venv/bin/opssum" ]; then
  fail "$launcher is a symlink to another location; refusing to overwrite it."
fi
if [ -e "$launcher" ] && [ ! -L "$launcher" ]; then
  fail "$launcher already exists and is not a symlink; refusing to overwrite it."
fi
ln -sfn "$venv/bin/opssum" "$launcher"

"$launcher" --version >/dev/null || fail "The dev command did not run after installation."
say "Installed $command_name from $root"
say "Run: $command_name (or $command_name init --examples)"
say "Run tests: $dev_python -m pytest -q"
case ":${PATH:-}:" in
  *":$bin_dir:"*) ;;
  *)
    say "$bin_dir is not on PATH; add this line to your shell profile, then open a new terminal:"
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    ;;
esac
