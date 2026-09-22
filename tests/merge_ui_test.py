#!/usr/bin/env python3
"""多轨合并的界面层验证：模式切换 / 声道前缀 / 排序 / 只出一个任务。

用法：
    python tests/merge_ui_test.py [截图输出目录]
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
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.style import apply_theme  # noqa: E402

PASS, FAIL = [], []
CHANS = ["FL", "FR", "FC", "LFE", "BL", "BR"]
FREQ = {"FL": 440, "FR": 550, "FC": 660, "LFE": 80, "BL": 770, "BR": 880}
# 故意打乱顺序，用来验证「按文件名排序」
SCRAMBLED = ["BR", "FC", "FL", "LFE", "FR", "BL"]


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✔' if ok else '✘'} {name}" + (f"  —— {detail}" if detail else ""))


def pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def pump_until(app: QApplication, cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)
        if cond():
            return True
    return False


def make_mono(ff: str, path: Path, freq: int, seconds: float = 2.0) -> bool:
    return subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"aevalsrc=0.3*sin({freq}*2*PI*t):s=48000:c=mono",
         "-t", str(seconds), str(path)], capture_output=True).returncode == 0


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        tempfile.mkdtemp(prefix="xiaowan_merge_ui_"))
    outdir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    ctx = AppContext()
    # 页面会把自己的模式写进配置，测试前后要还原用户原本的设置
    saved_merge = ctx.config.downmix_merge
    saved_layout = ctx.config.downmix_merge_layout
    saved_theme = ctx.config.theme
    ctx.config.downmix_merge = False
    ctx.config.downmix_merge_layout = "auto"
    apply_theme(app, ctx.config.theme)

    if not ctx.env.ready:
        print("找不到 ffmpeg，跳过")
        return 2

    window = MainWindow(ctx)
    window.resize(1180, 860)
    window.show()
    pump(app, 0.8)

    tmp = Path(tempfile.mkdtemp(prefix="xiaowan_merge_src_"))
    srcs = []
    print("[1] 准备 6 个单声道素材（文件名带声道标记，但顺序是乱的）")
    for role in SCRAMBLED:
        path = tmp / f"电影_{role}.wav"
        check(f"生成 {path.name}", make_mono(ctx.env.ffmpeg, path, FREQ[role]))
        srcs.append(path)

    page = window.downmix_page
    window.go_to(2)
    pump(app, 0.4)

    print("\n[2] 默认是「逐个文件」模式")
    check("默认逐个模式", not page._is_merge())
    check("合并设置区默认隐藏", not page.merge_block.isVisible())

    print("\n[3] 切到「多轨合并」")
    page.radio_merge.setChecked(True)
    pump(app, 0.3)
    check("模式已切换", page._is_merge())
    check("合并设置区已显示", page.merge_block.isVisible())
    check("输出格式自动落到 M4A",
          page.cmb_format.currentData() == "m4a", page.cmb_format.currentData())

    page.table.add_paths([str(p) for p in srcs])
    ready = pump_until(app, lambda: all(
        page.table.info_for(p) is not None for p in page.table.paths))
    check("6 个文件探测完成", ready)

    print("\n[4] 文件名列标出「这个文件会被当成哪条声道」")
    names = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    for row, (slot, role) in enumerate(zip(CHANS, SCRAMBLED)):
        check(f"第 {row + 1} 行标成槽位 {slot}（文件名是 {role}）",
              names[row].startswith(f"{slot} ") and f"电影_{role}.wav" in names[row],
              names[row])
    check("自动推断为 5.1 布局",
          page.cmb_layout.currentData() == "auto"
          and page._merge_roles() == CHANS, str(page._merge_roles()))
    check("槽位与文件名对不上的行被标黄", "标黄的 5 个文件" in page.lbl_merge.text(),
          page.lbl_merge.text()[-56:])
    from app.ui.style import palette as _pal
    warn_row = page.table.item(0, 0).foreground().color().name().lower()
    ok_row = page.table.item(3, 0).foreground().color().name().lower()
    check("标黄行用的确实是警示色", warn_row == _pal()["warn"].lower(),
          f"第1行 {warn_row} / 第4行 {ok_row}")
    check("顺序正确的行用正常文字色", ok_row == _pal()["text"].lower(), ok_row)

    print("\n[5] 「按文件名排序」把顺序排好")
    page.btn_sort.click()
    pump(app, 0.3)
    order = [Path(p).stem.split("_")[-1] for p in page.table.paths]
    check("顺序变成 FL → FR → FC → LFE → BL → BR", order == CHANS, str(order))
    names = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    check("表内前缀同步更新",
          names[0].startswith("FL ") and "电影_FL.wav" in names[0]
          and names[5].startswith("BR ") and "电影_BR.wav" in names[5],
          f"{names[0]} … {names[5]}")
    check("排好之后不再标黄", "标黄" not in page.lbl_merge.text(),
          page.lbl_merge.text()[-40:])

    print("\n[6] 底部提示说明只产出一个文件")
    hint = page.bar.hint.text()
    print(f"      {hint}")
    check("提示里点明「只产出 1 个文件」", "只产出 1 个文件" in hint, hint)
    check("按钮可用", page.bar.button.isEnabled())

    print("\n[7] 手动上移 / 下移")
    page.table.selectRow(0)
    page.btn_down.click()
    pump(app, 0.2)
    order = [Path(p).stem.split("_")[-1] for p in page.table.paths]
    check("下移把第 1 行挪到第 2 位", order[0] == "FR" and order[1] == "FL",
          str(order))
    page.table.selectRow(1)
    page.btn_up.click()
    pump(app, 0.2)
    order = [Path(p).stem.split("_")[-1] for p in page.table.paths]
    check("上移挪回去", order == CHANS, str(order))

    print("\n[8] 布局对不上时拦住用户")
    idx_71 = page.cmb_layout.findData("7.1")
    page.cmb_layout.setCurrentIndex(idx_71)
    pump(app, 0.3)
    check("选了 7.1 但只有 6 个文件 → 按钮禁用", not page.bar.button.isEnabled())
    check("给出红色提示", "对不上" in page.bar.hint.text(), page.bar.hint.text())
    check("合并说明也标红", page.lbl_merge.text().startswith("当前 6 个文件"),
          page.lbl_merge.text()[:40])
    page.cmb_layout.setCurrentIndex(page.cmb_layout.findData("auto"))
    pump(app, 0.3)
    check("改回自动后恢复可用", page.bar.button.isEnabled())

    print("\n[9] 点「开始下混」——6 个文件只应产出 1 个任务")
    captured: list = []
    original = ctx.queue.add_many

    def fake_add(tasks):
        captured.extend(tasks)

    ctx.queue.add_many = fake_add
    try:
        page.bar.button.click()
        pump(app, 0.4)
    finally:
        ctx.queue.add_many = original

    check("只生成了 1 个任务", len(captured) == 1, f"{len(captured)} 个")
    if captured:
        task = captured[0]
        check("输出名取共同词干", task.dst.name == "电影_立体声.m4a", task.dst.name)
        check("任务名点明合并了 6 路",
              task.name == "电影（6 路声道合并）", task.name)
        check("来源记录了 6 个文件", len(task.sources) == 6)

        print("\n[10] 真跑一次这个任务")
        r = subprocess.run(task.steps[0].args, capture_output=True)
        if r.returncode != 0:
            print("      ffmpeg:", r.stderr.decode("utf-8", "replace")[-300:])
        check("命令执行成功", r.returncode == 0)
        check("产出文件存在且只有它一个",
              task.dst.exists(), str(task.dst))
        if task.dst.exists():
            import wave
            wav = tmp / "check.wav"
            subprocess.run([ctx.env.ffmpeg, "-hide_banner", "-loglevel", "error",
                            "-y", "-i", str(task.dst), "-c:a", "pcm_s16le",
                            str(wav)], capture_output=True)
            with wave.open(str(wav), "rb") as w:
                check("输出是立体声", w.getnchannels() == 2,
                      f"{w.getnchannels()}ch")
                check("时长约 2s",
                      abs(w.getnframes() / w.getframerate() - 2.0) < 0.3,
                      f"{w.getnframes() / w.getframerate():.2f}s")

    print("\n[10.5] 合并模式下只放 1 个多声道文件 → 必须走普通下混")
    big = tmp / "整段电影_5.1.mkv"
    subprocess.run([ctx.env.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i",
                    "aevalsrc=sin(440*2*PI*t)|sin(550*2*PI*t)|sin(660*2*PI*t)"
                    "|sin(80*2*PI*t)|sin(770*2*PI*t)|sin(880*2*PI*t)"
                    ":s=48000:c=5.1", "-t", "2",
                    "-c:a", "ac3", "-b:a", "448k", str(big)],
                   capture_output=True)
    page.table.clear_all()
    page.table.add_paths([str(big)])
    pump_until(app, lambda: page.table.info_for(big) is not None)
    pump(app, 0.3)
    check("给出「按普通下混处理」提示", "普通下混" in page.lbl_merge.text(),
          page.lbl_merge.text()[:40])
    captured.clear()
    ctx.queue.add_many = fake_add
    try:
        page.bar.button.click()
        pump(app, 0.4)
    finally:
        ctx.queue.add_many = original
    check("仍然只生成 1 个任务", len(captured) == 1, f"{len(captured)} 个")
    if captured:
        steps_text = " ".join(" ".join(st.args) for st in captured[0].steps)
        check("命令里有 pan 混音矩阵（走了普通下混）", "pan=stereo" in steps_text,
              steps_text[:120])
        check("命令里没有 amerge（没有错误合并）", "amerge" not in steps_text)

    print("\n[11] 截图")
    page.table.clear_all()
    page.table.add_paths([str(p) for p in srcs])
    pump_until(app, lambda: all(
        page.table.info_for(p) is not None for p in page.table.paths))
    page.btn_sort.click()
    pump(app, 0.5)
    window._apply_theme("light")
    window.go_to(2)          # 点过「开始下混」会自动跳到队列页，跳回来
    pump(app, 0.5)
    light = outdir / "下混_多轨合并_浅色.png"
    window.grab().save(str(light))
    print(f"      saved {light}")

    window._apply_theme("dark")
    pump(app, 0.6)
    dark = outdir / "下混_多轨合并_深色.png"
    window.grab().save(str(dark))
    print(f"      saved {dark}")

    ctx.config.downmix_merge = saved_merge
    ctx.config.downmix_merge_layout = saved_layout
    ctx.config.theme = saved_theme
    ctx.save_config()

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  失败：{name}")
    print(f"截图目录：{outdir}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
