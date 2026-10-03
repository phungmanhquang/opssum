#!/usr/bin/env bash
# Cài agent-knowledge: ưu tiên pipx, rồi uv, cuối cùng venv riêng.
set -euo pipefail
cd "$(dirname "$0")"

if command -v pipx >/dev/null 2>&1; then
  # pipx chọn uv làm backend khi uv có sẵn. Với venv đã tồn tại, pipx
  # --force vẫn gọi `uv venv`; uv cần được yêu cầu xóa venv cũ trước.
  UV_VENV_CLEAR=1 pipx install --force .
elif command -v uv >/dev/null 2>&1; then
  uv tool install --force .
else
  PY=${PYTHON:-python3}
  "$PY" -c 'import sys; assert sys.version_info >= (3, 10), "cần Python >= 3.10"'
  VENV="${AGENT_KNOWLEDGE_VENV:-$HOME/.local/share/agent-knowledge/venv}"
  # --clear giúp script chạy lại được khi venv riêng đã tồn tại.
  "$PY" -m venv --clear "$VENV"
  "$VENV/bin/pip" install --upgrade pip >/dev/null
  "$VENV/bin/pip" install --force-reinstall .
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV/bin/agent-knowledge" "$HOME/.local/bin/agent-knowledge"
  ln -sf "$VENV/bin/ak" "$HOME/.local/bin/ak"
  echo "Đã link vào ~/.local/bin — đảm bảo thư mục này nằm trong PATH."
fi

echo
echo "Xong. Chạy:  agent-knowledge init --examples   rồi   agent-knowledge"
