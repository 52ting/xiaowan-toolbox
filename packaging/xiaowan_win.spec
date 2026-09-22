# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（在 Windows 上构建免安装 exe）。

用法（项目根目录）：
    pyinstaller packaging/xiaowan_win.spec --noconfirm

产物：
    dist/小丸工具箱/小丸工具箱.exe      双击即用，无需装 Python
    （onedir 模式：启动快、体积可控；拷整个文件夹即可分发）

如果仓库根目录存在 bin/ffmpeg.exe 与 bin/ffprobe.exe，会被一并打进包里，
实现"开箱即用、无需另外安装 ffmpeg"。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # packaging/ 的上一级
bin_dir = ROOT / "bin"
ffmpeg = bin_dir / "ffmpeg.exe"
ffprobe = bin_dir / "ffprobe.exe"
ico = ROOT / "packaging" / "icon.ico"

datas = [(str(ROOT / "app" / "resources" / "icon.png"), "app/resources")]
if ffmpeg.is_file():
    datas.append((str(ffmpeg), "bin"))
if ffprobe.is_file():
    datas.append((str(ffprobe), "bin"))

# 只保留真正用到的 Qt 模块，其余整块排除，能砍掉上百 MB
EXCLUDES = [
    "tkinter", "matplotlib", "numpy", "scipy", "pandas",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtNetworkAuth", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.Qt3DCore",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtDBus",
    "PySide6.QtConcurrent", "PySide6.QtXml", "PySide6.QtSvgWidgets",
    "PySide6.QtLinguistics", "PySide6.QtPrintSupport",
    "imageio_ffmpeg",                   # 仅开发期兜底用，打包后走内置 bin/ffmpeg.exe
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
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
    console=False,                      # 关键：不弹黑色命令行窗口
    disable_windowed_traceback=False,
    icon=str(ico) if ico.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="小丸工具箱",
)
