#!/usr/bin/env bash
# =============================================================================
# 小丸工具箱 · Mac 版 —— 一键构建脚本（必须在 macOS 上执行）
#
# 产物：
#   dist/小丸工具箱.app          独立应用，双击即用
#   dist/小丸工具箱.dmg          （可选，加 --dmg）磁盘映像，方便分发
#
# 用法：
#   bash packaging/build_macos.sh           # 标准构建
#   bash packaging/build_macos.sh --dmg     # 构建并生成 DMG
#   bash packaging/build_macos.sh --no-ffmpeg   # 不打包 ffmpeg（用户需自备）
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_NAME="小丸工具箱"
PY="${PYTHON:-python3}"
WANT_DMG=0
BUNDLE_FFMPEG=1

for arg in "$@"; do
  case "$arg" in
    --dmg) WANT_DMG=1 ;;
    --no-ffmpeg) BUNDLE_FFMPEG=0 ;;
  esac
done

echo "==> 1/6 准备虚拟环境"
if [ ! -x ".buildenv/bin/python" ]; then
  "$PY" -m venv .buildenv
fi
.buildenv/bin/python -m pip install --upgrade pip -q
.buildenv/bin/python -m pip install -r requirements.txt pyinstaller -q

echo "==> 2/6 准备 ffmpeg / ffprobe"
if [ "$BUNDLE_FFMPEG" = "1" ]; then
  mkdir -p bin
  if [ ! -x bin/ffmpeg ]; then
    if command -v ffmpeg >/dev/null 2>&1; then
      FFMPEG_SRC="$(command -v ffmpeg)"
    elif [ -x /opt/homebrew/bin/ffmpeg ]; then
      FFMPEG_SRC="/opt/homebrew/bin/ffmpeg"
    elif [ -x /usr/local/bin/ffmpeg ]; then
      FFMPEG_SRC="/usr/local/bin/ffmpeg"
    else
      echo "  !! 未找到 ffmpeg。请先安装： brew install ffmpeg"
      echo "     或使用 --no-ffmpeg 跳过内置（用户首次使用时需自备）。"
      exit 1
    fi
    echo "  复制 $FFMPEG_SRC -> bin/"
    cp "$FFMPEG_SRC" bin/ffmpeg
    [ -f "${FFMPEG_SRC%ffmpeg}ffprobe" ] && cp "${FFMPEG_SRC%ffmpeg}ffprobe" bin/ffprobe || true
  fi
  chmod +x bin/ffmpeg bin/ffprobe 2>/dev/null || true
fi

echo "==> 3/6 生成图标与应用 .icns"
.buildenv/bin/python packaging/make_icon.py
if [ "$(uname)" = "Darwin" ] && [ -f app/resources/icon.png ]; then
  ICONSET="$(mktemp -d)/icon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 64 128 256 512 1024; do
    sips -z $size $size app/resources/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    [ "$double" -le 1024 ] && sips -z $double $double app/resources/icon.png \
      --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null || true
  done
  iconutil -c icns "$ICONSET" -o packaging/icon.icns
  echo "  packaging/icon.icns 已生成"
fi

echo "==> 4/6 PyInstaller 打包"
.buildenv/bin/python -m PyInstaller packaging/xiaowan.spec --noconfirm --clean
rm -rf "dist/$APP_NAME.app"
mv "dist/$APP_NAME" "dist/$APP_NAME.app"

echo "==> 5/6 清理临时转储"
if [ -x "dist/$APP_NAME.app/Contents/MacOS/$APP_NAME" ]; then
  codesign --force --deep --sign - "dist/$APP_NAME.app" 2>/dev/null || true
fi

echo "==> 6/6 完成"
ls -la dist/ || true

if [ "$WANT_DMG" = "1" ]; then
  echo "==> 生成 DMG"
  hdiutil create -volname "$APP_NAME" -srcfolder "dist/$APP_NAME.app" \
    -ov -format UDZO "dist/$APP_NAME.dmg"
  echo "dist/$APP_NAME.dmg 已生成"
fi

cat <<TIP

构建完成：dist/$APP_NAME.app

首次打开如果被 Gatekeeper 拦下（未公证），在终端执行：
    xattr -cr "dist/$APP_NAME.app"
或者在「系统设置 → 隐私与安全性」里点「仍要打开」。

TIP
