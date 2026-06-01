#!/bin/zsh
# QR本地知识库 启动器：确保 Web 服务可用后打开浏览器
set -euo pipefail

QR_BIN="$(command -v qr 2>/dev/null || true)"
PORT=8765
if [[ -f "$HOME/.qr/config.json" ]]; then
  PORT="$(/usr/bin/python3 -c "
import json
from pathlib import Path
p = Path.home() / '.qr' / 'config.json'
print(json.loads(p.read_text()).get('web_port', 8765))
" 2>/dev/null || echo 8765)"
fi
URL="http://127.0.0.1:${PORT}"
mkdir -p "$HOME/.qr/logs"

_up() {
  /usr/bin/curl -s -o /dev/null --max-time 2 "$URL/api/status"
}

if ! _up; then
  UID_NUM="$(/usr/bin/id -u)"
  LABEL="gui/${UID_NUM}/com.qr.web"
  if /bin/launchctl print "$LABEL" &>/dev/null; then
    /bin/launchctl kickstart -k "$LABEL" 2>/dev/null || true
  elif [[ -n "$QR_BIN" && -x "$QR_BIN" ]]; then
    /usr/bin/nohup "$QR_BIN" web --port "$PORT" >>"$HOME/.qr/logs/web.log" 2>&1 &
  else
    /usr/bin/osascript -e 'display alert "QR本地知识库未安装" message "找不到 qr 命令。请在终端执行：conda activate qr（或 kb）后 pip install -e ~/Projects/qr，并运行 qr web --install"'
    exit 1
  fi
  for _ in {1..40}; do
    /bin/sleep 0.5
    _up && break
  done
fi

if ! _up; then
  /usr/bin/osascript -e 'display alert "QR本地知识库启动失败" message "Web 服务未响应，请在终端运行：qr web --restart"'
  exit 1
fi

/usr/bin/open "$URL"
