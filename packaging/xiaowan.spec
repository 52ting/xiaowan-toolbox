# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（在 macOS 上构建 .app）。

用法（项目根目录）：
    pyinstaller packaging/xiaowan.spec --noconfirm

如果仓库根目录存在 bin/ffmpeg 与 bin/ffprobe，会被一并打进 .app，
实现"开箱即用、无需 brew install ffmpeg"。
"""

import os
import re
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # packaging/ 的上一级
bin_dir = ROOT / "bin"
ffmpeg = bin_dir / "ffmpeg"
ffprobe = bin_dir / "ffprobe"
icon = ROOT / "packaging" / "icon.icns"

# 版本号与 app/__init__.py 保持同步，避免 .app 里写死旧版本
_version = "1.0.0"
try:
    _m = re.search(r'__version__\s*=\s*"([^"]+)"',
                   (ROOT / "app" / "__init__.py").read_text(encoding="utf-8"))
    if _m:
        _version = _m.group(1)
except Exception:
    pass

datas = [(str(ROOT / "app" / "resources" / "icon.png"), "app/resources")]
if ffmpeg.is_file():
    datas.append((str(ffmpeg), "bin"))
if ffprobe.is_file():
    datas.append((str(ffprobe), "bin"))

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.distutils"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="小丸工具箱",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                      # 关键：不弹终端窗口
    disable_windowed_traceback=False,
    icon=str(icon) if icon.is_file() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="小丸工具箱",
)

app = BUNDLE(
    coll,
    name="小丸工具箱.app",
    icon=str(icon) if icon.is_file() else None,
    bundle_identifier="com.xiaowan.toolbox",
    info_plist={
        "CFBundleName": "小丸工具箱",
        "CFBundleDisplayName": "小丸工具箱",
        "CFBundleShortVersionString": _version,
        "CFBundleVersion": _version,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "基于 FFmpeg，遵循 LGPL/GPL 许可分发",
        "NSPrincipalClass": "NSApplication",
        "LSApplicationCategoryType": "public.app-category.video",
    },
)
