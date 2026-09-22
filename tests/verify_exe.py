#!/usr/bin/env python3
"""打包产物冒烟测试：启动 exe，等窗口出现，截图，再关掉。

打包成无控制台的 exe 之后，出问题会静默退出、什么都看不到，
所以这里必须真的把它跑起来并截一张图确认界面渲染出来了。

用法：
    python tests/verify_exe.py [exe路径] [截图目录]
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLE = "小丸工具箱"

# Windows 下 stdout 被重定向到管道/文件时，Python 会退回 GBK（cp936），
# 打印下面的 ▶ / ✓ / ✗ 会直接抛 UnicodeEncodeError 把脚本打断。
# 显式切到 UTF-8，让脚本在 CI、重定向、被别的程序调用时都能跑。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass


def _find_window(title: str) -> int:
    return ctypes.windll.user32.FindWindowW(None, title)  # type: ignore[attr-defined]


def _find_window_for_pid(target_pid: int) -> int:
    """按进程 ID 找可见主窗口（标题匹配会被同名资源管理器窗口撞上）。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):
        pid = ctypes.c_uint32()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == target_pid and user32.IsWindowVisible(hwnd):
            # 只要主窗口（有标题栏的顶层窗口）
            if user32.GetWindowLongW(hwnd, -16) & 0x00C80000:  # WS_CAPTION
                found.append(hwnd)
        return 1

    user32.EnumWindows(_cb, None)
    return found[0] if found else 0


def _capture_window(hwnd: int, w: int, h: int):
    """PrintWindow 直接抓取目标窗口内容，即使被其他窗口遮挡也能截到。"""
    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)  # 2 = PW_RENDERFULLCONTENT

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    bi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return img


def main() -> int:
    # 关键：让本进程 DPI 感知。否则在高分屏（如 150% 缩放）上
    # GetWindowRect 返回的是"虚拟化"的逻辑坐标，而 ImageGrab 抓的是物理像素，
    # 两者相减会裁出比真实窗口小一圈的区域，看起来就像界面缺了一块。
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "dist" / TITLE / f"{TITLE}.exe")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "_shots"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not exe.is_file():
        print(f"✗ 找不到可执行文件：{exe}")
        return 1

    print(f"▶ 启动 {exe}")
    print(f"  所在目录体积："
          f"{sum(f.stat().st_size for f in exe.parent.rglob('*') if f.is_file()) / 1048576:.1f} MB")

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))

    # 等窗口出现
    hwnd = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"✗ 进程提前退出，退出码 {proc.returncode}")
            log = Path.home() / "AppData" / "Roaming" / "XiaoWanToolbox" / "crash.log"
            if log.is_file():
                print("\n--- crash.log ---")
                print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
            return 1
        hwnd = _find_window_for_pid(proc.pid)
        if hwnd:
            break
        time.sleep(0.3)

    if not hwnd:
        print("✗ 60 秒内没有出现窗口")
        proc.terminate()
        return 1

    print("✓ 窗口已出现")
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.ShowWindow(hwnd, 5)              # SW_SHOW
    user32.SetForegroundWindow(hwnd)
    time.sleep(2.5)                         # 等界面画完

    # 窗口区域截图
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print(f"  窗口位置：({rect.left},{rect.top}) - ({rect.right},{rect.bottom})")

    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w > 0 and h > 0:
        crop = out_dir / "exe_窗口.png"
        _capture_window(hwnd, w, h).convert("RGB").save(crop)
        print(f"✓ 窗口截图（PrintWindow）：{crop}")

    # 活着且渲染成功，收工
    time.sleep(0.5)
    still_alive = proc.poll() is None
    print(f"✓ 截图后进程仍在运行：{still_alive}")
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("✓ 已关闭")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
