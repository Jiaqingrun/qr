#!/bin/bash
# 构建 macOS 桌面应用 QR本地知识库.app
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_TITLE="QR本地知识库"
BUNDLE_ID="com.qr.launcher"
BUILD_DIR="$SCRIPT_DIR/build"
APP_PATH="$BUILD_DIR/${APP_TITLE}.app"
ICON_PNG="$SCRIPT_DIR/qr-icon-1024.png"
ICON_SVG="$SCRIPT_DIR/qr-icon.svg"
DESKTOP="${INSTALL_DESKTOP:-$HOME/Desktop}"

if [[ -n "${ICON_SRC:-}" ]]; then
  :
elif [[ -f "$ICON_PNG" ]]; then
  ICON_SRC="$ICON_PNG"
elif [[ -f "$ICON_SVG" ]] && command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert -w 1024 -h 1024 "$ICON_SVG" -o "$ICON_PNG"
  ICON_SRC="$ICON_PNG"
else
  echo "缺少图标: $ICON_PNG（或安装 rsvg-convert 以从 qr-icon.svg 渲染）" >&2
  exit 1
fi

# 生成模型常输出非正方形画布，直接 -z 会压扁；先居中裁切为正方形再缩放
SQUARE_PNG="$BUILD_DIR/icon-square-1024.png"
mkdir -p "$BUILD_DIR"
W=$(sips -g pixelWidth "$ICON_SRC" 2>/dev/null | awk '/pixelWidth/{print $2}')
H=$(sips -g pixelHeight "$ICON_SRC" 2>/dev/null | awk '/pixelHeight/{print $2}')
if [[ -z "$W" || -z "$H" ]]; then
  echo "无法读取图标尺寸: $ICON_SRC" >&2; exit 1
fi
if [[ "$W" != "$H" ]]; then
  SIDE=$(( W < H ? W : H ))
  echo "图标 ${W}×${H} 非正方形，居中裁切为 ${SIDE}×${SIDE}"
  sips -c "$SIDE" "$SIDE" "$ICON_SRC" --out "$SQUARE_PNG" >/dev/null
else
  cp "$ICON_SRC" "$SQUARE_PNG"
fi
sips -z 1024 1024 "$SQUARE_PNG" --out "$SQUARE_PNG" >/dev/null
ICON_SRC="$SQUARE_PNG"
# 回写标准源文件，避免下次构建再次压扁
cp "$SQUARE_PNG" "$ICON_PNG"

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"

cp "$SCRIPT_DIR/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$SCRIPT_DIR/qr-launcher.zsh" "$APP_PATH/Contents/MacOS/qr"
chmod +x "$APP_PATH/Contents/MacOS/qr"

ICONSET="$BUILD_DIR/qr.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  dbl=$((size * 2))
  sips -z "$dbl" "$dbl" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP_PATH/Contents/Resources/qr.icns"

echo "已构建: $APP_PATH"

if [[ "${1:-}" == "--install" || "${1:-}" == "--desktop" ]]; then
  OLD_KB="$DESKTOP/kb.app"
  OLD_QR="$DESKTOP/${APP_TITLE}.app"
  [[ -d "$OLD_KB" ]] && rm -rf "$OLD_KB"
  rm -rf "$OLD_QR"
  cp -R "$APP_PATH" "$OLD_QR"
  /usr/bin/touch "$OLD_QR"
  echo "已安装到桌面: $OLD_QR"
fi
