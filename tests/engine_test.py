#!/usr/bin/env python3
"""引擎测试：任务队列 / 进度信号 / 并发 / 取消（不打开窗口）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from app.core.config import AppConfig  # noqa: E402
from app.core.engine import QueueManager  # noqa: E402
from app.core.ffmpeg import FFmpegEnv  # noqa: E402
from app.core.pipeline import build_audio_task, build_compress_task  # noqa: E402
from app.core.presets import PRESETS_BY_KEY  # noqa: E402
from app.core.task import TaskStatus  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail else ""))


def make_sample(env, path: Path, seconds: int) -> bool:
    return subprocess.run([
        env.ffmpeg, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", str(path),
    ], capture_output=True).returncode == 0


def run_queue(mgr: QueueManager, timeout_s: float) -> None:
    loop = QCoreApplication.instance()
    holder = {"done": False}
    mgr.queueEmpty.connect(lambda: holder.update(done=True))
    deadline = time.time() + timeout_s
    while not holder["done"] and time.time() < deadline:
        loop.processEvents()
        time.sleep(0.02)
    if not holder["done"]:
        print("  （等待超时，强制继续）")


def main() -> int:
    app = QCoreApplication(sys.argv[:1])
    env = FFmpegEnv().refresh()
    if not env.ready:
        print("找不到 ffmpeg")
        return 2
    print(f"ffmpeg: {env.ffmpeg}\n")

    work = Path(tempfile.mkdtemp(prefix="xiaowan_engine_"))
    src = work / "input.mp4"
    if not make_sample(env, src, 20):
        print("素材生成失败")
        return 2

    cfg = AppConfig()
    cfg.output_dir = str(work / "out")
    cfg.output_mode = "custom"
    cfg.concurrency = 2

    mgr = QueueManager(lambda: env, lambda: cfg)

    updates: list[float] = []
    mgr.taskUpdated.connect(lambda tid: updates.append(mgr.get(tid).progress)
                            if mgr.get(tid) else None)

    # ---------------- 场景 1：批量 + 进度 ---------------- #
    print("[1] 批量压缩 + 进度上报")
    settings = PRESETS_BY_KEY["fast"].video
    tasks = [build_compress_task(type("C", (), {"env": env, "config": cfg})(),
                                 src, None, settings) for _ in range(3)]
    for t in tasks:
        if t.info is None:
            from app.core.ffmpeg import probe
            t.info = probe(env, src)
    mgr.add_many(tasks)
    run_queue(mgr, 120)

    ok = all(t.status == TaskStatus.DONE for t in tasks)
    check("3 个任务全部完成", ok,
          "、".join(f"{t.status.value}:{t.progress:.0%}" for t in tasks))
    check("输出文件都存在", all(t.dst.exists() for t in tasks),
          f"{sum(t.out_size for t in tasks) / 1024:.0f} KB 合计")
    check("进度最终到 100%", all(abs(t.progress - 1.0) < 1e-6 for t in tasks))
    check("进度是渐进上报的", len(updates) > 10, f"{len(updates)} 次刷新")
    check("速度信息已解析", any(t.speed for t in tasks),
          "、".join(sorted({t.speed for t in tasks if t.speed})))
    check("剩余时间已解析", any(t.eta >= 0 for t in tasks))
    check("压缩比合理", all(0.01 < t.ratio < 1.5 for t in tasks),
          "、".join(f"{t.ratio:.0%}" for t in tasks))

    # ---------------- 场景 2：音频任务混排 ---------------- #
    print("\n[2] 压缩 + 音频混合队列")
    from app.core.presets import AUDIO_FORMATS
    a_task = build_audio_task(type("C", (), {"env": env, "config": cfg})(),
                              src, None, "mp3", "libmp3lame", 192)
    mgr.add(a_task)
    run_queue(mgr, 60)
    check("音频任务完成", a_task.status == TaskStatus.DONE and a_task.dst.exists(),
          a_task.dst.name if a_task.dst.exists() else str(a_task.error))

    # ---------------- 场景 3：取消 ---------------- #
    print("\n[3] 运行中取消")
    slow = VideoSettingsLike = PRESETS_BY_KEY["tiny"].video  # x265 veryslow，足够慢
    big = work / "big.mp4"
    make_sample(env, big, 60)
    cancel_task = build_compress_task(type("C", (), {"env": env, "config": cfg})(),
                                      big, None, slow)
    from app.core.ffmpeg import probe
    cancel_task.info = probe(env, big)
    mgr.add(cancel_task)
    deadline = time.time() + 20
    while time.time() < deadline and cancel_task.status != TaskStatus.RUNNING:
        app.processEvents()
        time.sleep(0.02)
    time.sleep(1.0)
    app.processEvents()
    mgr.cancel(cancel_task.id)
    run_queue(mgr, 30)
    check("任务被取消", cancel_task.status == TaskStatus.CANCELED,
          cancel_task.status.value)
    check("半成品文件已清理", not cancel_task.dst.exists(),
          cancel_task.dst.name)

    # ---------------- 场景 4：并发限制 ---------------- #
    print("\n[4] 并发数限制")
    cfg.concurrency = 1
    t1 = build_compress_task(type("C", (), {"env": env, "config": cfg})(), big, None, slow)
    t2 = build_compress_task(type("C", (), {"env": env, "config": cfg})(), big, None, slow)
    for t in (t1, t2):
        t.info = probe(env, big)
    mgr.add_many([t1, t2])
    deadline = time.time() + 15
    while time.time() < deadline and mgr.running_count() < 1:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    check("并发=1 时只跑一个任务",
          mgr.running_count() == 1 and mgr.waiting_count() == 1,
          f"running={mgr.running_count()} waiting={mgr.waiting_count()}")
    mgr.clear_all()
    run_queue(mgr, 10)

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  失败：{name}")
    print(f"测试目录：{work}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
