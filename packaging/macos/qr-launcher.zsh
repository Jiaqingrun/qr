#!/bin/zsh
# QR本地知识库 启动器：Finder 双击时 PATH 不含 conda，须固定解析 qr 入口
set -euo pipefail

_CONDA_QR="/opt/anaconda3/envs/qr/bin/qr"
_CONDA_PY="/opt/anaconda3/envs/qr/bin/python"

QR_ARGS=()
if [[ -x "$_CONDA_QR" ]]; then
  QR_ARGS=("$_CONDA_QR")
elif [[ -x "$_CONDA_PY" ]]; then
  QR_ARGS=("$_CONDA_PY" -m qr.cli)
else
  QR_BIN="$(command -v qr 2>/dev/null || true)"
  if [[ -n "$QR_BIN" && -x "$QR_BIN" && "$QR_BIN" != *"/envs/kb/"* ]]; then
    QR_ARGS=("$QR_BIN")
  fi
fi

if [[ ${#QR_ARGS[@]} -eq 0 ]]; then
  /usr/bin/osascript -e 'display alert "QR本地知识库未安装" message "找不到 qr 命令。请在终端执行：conda activate qr 后 pip install -e ~/QR/dev/qr，并运行 qr web --install"'
  exit 1
fi

exec "${QR_ARGS[@]}" desktop --open
