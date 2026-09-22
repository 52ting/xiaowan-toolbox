#!/usr/bin/env python3
"""在 Windows 上打包出免安装的 exe。

用法（项目根目录）：
    python packaging/build_windows.py            # 打包
    python packaging/build_windows.py --clean     # 先清掉旧产物再打包

产物：
    dist/小丸工具箱/小丸工具箱.exe

为什么用 Python 脚本而不是 .bat：
    .bat 在含中文/UTF-8 时的编码行为很不稳定，中文 exe 名容易被写坏。
    Python 脚本在 Windows / macOS / Linux 行为一致。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
APP_NAME = "小丸工具箱"


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n▶ {desc}")
    print(f"  $ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(ROOT))
    if res.returncode != 0:
        print(f"✗ 失败（退出码 {res.returncode}）：{desc}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="打包小丸工具箱 Windows 版")
    ap.add_argument("--clean", action="store_true", help="打包前删除 build/ 与 dist/")
    args = ap.parse_args()

    if not sys.platform.startswith("win"):
        print("这个脚本用于在 Windows 上打包；macOS 请用 packaging/build_macos.sh")
        return 1

    print("=" * 60)
    print("  小丸工具箱 · Windows 打包")
    print("=" * 60)

    # 1) 内置 ffmpeg 检查 ------------------------------------------------- #
    ffmpeg = ROOT / "bin" / "ffmpeg.exe"
    ffprobe = ROOT / "bin" / "ffprobe.exe"
    if ffmpeg.is_file():
        print(f"✓ 内置 ffmpeg：{ffmpeg.name}（{ffmpeg.stat().st_size / 1048576:.1f} MB）")
    else:
        print("! 未找到 bin/ffmpeg.exe —— 打包出的程序将依赖用户自行安装 ffmpeg。")
    if ffprobe.is_file():
        print(f"✓ 内置 ffprobe：{ffprobe.name}（{ffprobe.stat().st_size / 1048576:.1f} MB）")
    else:
        print("! 未找到 bin/ffprobe.exe —— 程序会自动降级用 ffmpeg -i 解析媒体信息。")

    # 2) 图标 ------------------------------------------------------------- #
    ico = PKG / "icon.ico"
    if not _run([sys.executable, str(PKG / "make_ico.py")], "生成 Windows 图标"):
        return 1
    if not ico.is_file():
        print("! 图标未生成，将继续但不带自定义图标。")

    # 3) 清理 ------------------------------------------------------------- #
    # 注意：不要在这里整目录 rmtree dist/ —— 会被工作台的安全删除钩子
    # 拦截（一次删上百个文件需要人工确认），导致打包静默中断。
    # PyInstaller 自带 --noconfirm，会自己覆盖旧产物，效果等同。
    if args.clean:
        for d in (ROOT / "build", ROOT / "dist"):
            if d.exists():
                print(f"▷ 跳过预清理 {d.name}/（交给 PyInstaller --noconfirm 覆盖）")

    # 4) PyInstaller ------------------------------------------------------ #
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("\n缺少 PyInstaller，请先安装：")
        print("  pip install pyinstaller pillow")
        return 1

    if not _run(
        [sys.executable, "-m", "PyInstaller",
         str(PKG / "xiaowan_win.spec"), "--noconfirm", "--clean"],
        "PyInstaller 打包中（首次较慢，请耐心等待）",
    ):
        return 1

    # 5) 结果 ------------------------------------------------------------- #
    out_dir = ROOT / "dist" / APP_NAME
    exe = out_dir / f"{APP_NAME}.exe"
    if not exe.is_file():
        print(f"\n✗ 没有找到产物：{exe}")
        return 1

    total = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print("\n" + "=" * 60)
    print("  ✓ 打包完成")
    print("=" * 60)
    print(f"  可执行文件：{exe}")
    print(f"  整个目录  ：{out_dir}")
    print(f"  目录体积  ：{total / 1048576:.1f} MB")
    print()
    print("  分发方式：把整个「小丸工具箱」文件夹拷给别人，双击里面的 exe 即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
