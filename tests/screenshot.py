#!/usr/bin/env python3
"""启动应用并逐页截图，用于核对界面布局。

用法：
    python tests/screenshot.py [输出目录]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.context import AppContext  # noqa: E402
from app.core.ffmpeg import FFmpegEnv  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.style import apply_theme  # noqa: E402

PAGES = ["压缩", "音频", "下混", "封装", "队列", "设置"]


def make_sample(env: FFmpegEnv, path: Path, seconds: int = 30) -> bool:
    res = subprocess.run([
        env.ffmpeg, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(path),
    ], capture_output=True)
    return res.returncode == 0


def make_surround_sample(env: FFmpegEnv, path: Path, seconds: int = 20) -> bool:
    """5.1 素材，给声道下混页用。"""
    res = subprocess.run([
        env.ffmpeg, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=320:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "ac3", "-b:a", "448k", "-ac", "6",
        str(path),
    ], capture_output=True)
    return res.returncode == 0


def pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(
        prefix="xiaowan_shots_"))
    outdir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    ctx = AppContext()
    apply_theme(app, ctx.config.theme)

    window = MainWindow(ctx)
    window.resize(1180, 800)
    window.show()
    pump(app, 0.8)

    # 造两个素材塞进压缩页，让截图里能看到真实数据
    env = ctx.env
    shots: list[tuple[str, str]] = []
    if env.ready:
        tmp = Path(tempfile.mkdtemp(prefix="xiaowan_demo_"))
        first = tmp / "产品宣传片_4K素材.mp4"
        second = tmp / "会议录屏_2026-09-08.mp4"
        surround = tmp / "蓝光原盘_5.1音轨.mkv"
        if make_sample(env, first, 30):
            make_sample(env, second, 20)
            window.compress_page.table.add_paths([str(first), str(second)])
            window.audio_page.table.add_paths([str(first)])
            window.remux_page.table.add_paths([str(first), str(second)])
            if make_surround_sample(env, surround, 20):
                window.downmix_page.table.add_paths([str(surround)])
            pump(app, 3.0)  # 等异步探测完成
            shots.append((str(first), str(second)))

    for i, name in enumerate(PAGES):
        window.go_to(i)
        pump(app, 0.5)
        target = outdir / f"{i + 1}_{name}.png"
        window.grab().save(str(target))
        print(f"saved {target}")

    # 深色主题也看一眼
    window._apply_theme("dark")
    window.go_to(0)
    pump(app, 0.6)
    dark = outdir / f"{len(PAGES) + 1}_深色主题.png"
    window.grab().save(str(dark))
    print(f"saved {dark}")

    print(f"\n截图目录：{outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
