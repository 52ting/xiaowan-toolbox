#!/usr/bin/env python3
"""声道下混端到端实测：真跑 ffmpeg，验证输出的声道数与混音内容。"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.commands import (build_downmix_steps, build_merge_downmix_steps,
                               channel_roles, merge_roles)  # noqa: E402
from app.core.config import AppConfig  # noqa: E402
from app.core.ffmpeg import FFmpegEnv, probe  # noqa: E402
from app.core.pipeline import build_merge_downmix_task  # noqa: E402
from app.core.presets import DOWNMIX_PRESETS_BY_KEY, DownmixSettings  # noqa: E402

PASS, FAIL = [], []
FREQ = [440, 550, 660, 80, 770, 880, 990, 1100]
LABEL = ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"]
ROLE_FREQ = dict(zip(LABEL, FREQ))
TOTAL = len(LABEL)


class _FakeCtx:
    """任务装配只需要 env 和 config 两样东西。"""

    def __init__(self, env, config):
        self.env = env
        self.config = config

    def save_config(self) -> None:
        return None


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✔' if ok else '✘'} {name}" + (f"  —— {detail}" if detail else ""))


def goertzel(samples, rate, freq) -> float:
    n = len(samples)
    k = int(0.5 + n * freq / rate)
    w = 2 * math.pi * k / n
    coeff = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return math.sqrt(max(s1 * s1 + s2 * s2 - coeff * s1 * s2, 0.0)) * 2 / n


def read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        rate, nch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    chans = [[] for _ in range(nch)]
    for i in range(0, n, 4):
        for c in range(nch):
            off = (i * nch + c) * 2
            v = int.from_bytes(raw[off:off + 2], "little", signed=True)
            chans[c].append(v / 32768.0)
    return rate // 4, chans


def dominant(chans, rate, count):
    """各声道占主导的频率标签。"""
    out = []
    for ch in chans:
        best, amp = "?", 0.0
        for i in range(count):
            a = goertzel(ch, rate, FREQ[i])
            if a > amp:
                best, amp = LABEL[i], a
        out.append(best)
    return out


def components(chans, rate, count, thresh: float = 0.18):
    """各声道里能量明显存在的频率标签集合。"""
    out = []
    for ch in chans:
        hits = []
        for i in range(count):
            if goertzel(ch, rate, FREQ[i]) > thresh:
                hits.append(LABEL[i])
        out.append(hits)
    return out


def make_src(ff: str, path: Path, layout: str, roles: list[str],
             with_video: bool, seconds: int = 2) -> bool:
    """按声道『语义』分配频率，这样索引错位会立刻暴露出来。"""
    expr = "|".join(f"sin({ROLE_FREQ[r]}*2*PI*t)" for r in roles)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y"]
    if with_video:
        cmd += ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25"]
    cmd += ["-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:c={layout}", "-t",
            str(seconds)]
    if with_video:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-shortest"]
    cmd += ["-c:a", "aac", "-b:a", "384k", str(path)]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def run_steps(env, src, dst, settings, info, container, codec, br):
    steps = build_downmix_steps(
        env, src, dst, settings, info, audio_codec=codec,
        audio_bitrate_kbps=br, container=container)
    r = subprocess.run(steps[0].args, capture_output=True)
    if r.returncode != 0:
        print(f"    ffmpeg 报错: {r.stderr.decode('utf-8', 'replace')[-400:]}")
    return r.returncode == 0


def extract_wav(ff: str, src: Path, dst: Path) -> bool:
    return subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dst)],
        capture_output=True).returncode == 0


def main() -> int:
    env = FFmpegEnv().refresh()
    if not env.ready:
        print("找不到 ffmpeg")
        return 2
    ff = env.ffmpeg
    work = Path(tempfile.mkdtemp(prefix="xiaowan_downmix_"))
    print(f"ffmpeg: {ff}\n工作目录: {work}\n")

    std = DOWNMIX_PRESETS_BY_KEY["standard"].settings

    # ---------------- 1. 5.1 视频 → 立体声 MP4 ---------------- #
    print("[1] 5.1 视频 → 立体声 MP4（画面应原样保留）")
    src = work / "src51.mp4"
    assert make_src(ff, src, "5.1", channel_roles("5.1", 6), True), "素材生成失败"
    info = probe(env, src)
    check("探测到 5.1", info.audios[0].channel_layout == "5.1",
          f"{info.audios[0].channel_layout} / {info.audios[0].channels}ch")
    dst = work / "out51.mp4"
    ok = run_steps(env, src, dst, std, info, "mp4", "aac", 192)
    check("命令执行成功", ok)
    if ok:
        out = probe(env, dst)
        check("输出音频是 2 声道", out.audios and out.audios[0].channels == 2,
              f"{out.audios[0].channels}ch / {out.audios[0].channel_layout}"
              if out.audios else "无音轨")
        check("输出保留了视频流", out.video is not None, out.resolution)
        # 视频流必须是 copy（时长/分辨率一致）
        check("视频分辨率未变", out.resolution == info.resolution,
              f"{info.resolution} → {out.resolution}")
        w = work / "out51.wav"
        if extract_wav(ff, dst, w):
            rate, chans = read_wav(w)
            comp = components(chans, rate, TOTAL)
            left, right = set(comp[0]), set(comp[1])
            check("左声道混入 FL + FC + BL",
                  {"FL", "FC", "BL"} <= left, f"{sorted(left)}")
            check("右声道混入 FR + FC + BR",
                  {"FR", "FC", "BR"} <= right, f"{sorted(right)}")
            check("左声道不含右后环绕 BR", "BR" not in left, f"{sorted(left)}")
            check("标准方案丢弃了低音炮 LFE", "LFE" not in left | right,
                  f"{sorted(left | right)}")

    # ---------------- 2. 7.1 音频 → 立体声 ---------------- #
    print("\n[2] 7.1 音频 → 立体声（应包含侧环绕成分）")
    src71 = work / "src71.m4a"
    assert make_src(ff, src71, "7.1", channel_roles("7.1", 8), False), "7.1 素材生成失败"
    i71 = probe(env, src71)
    check("探测到 7.1", i71.audios[0].channel_layout == "7.1",
          f"{i71.audios[0].channel_layout} / {i71.audios[0].channels}ch")
    dst71 = work / "out71.m4a"
    ok = run_steps(env, src71, dst71, std, i71, "m4a", "aac", 256)
    check("7.1 下混成功", ok and dst71.exists())
    if ok:
        o71 = probe(env, dst71)
        check("7.1 输出为 2 声道", o71.audios and o71.audios[0].channels == 2)

    # ---------------- 3. 5.0（无 LFE）→ 立体声 ---------------- #
    print("\n[3] 5.0 音频 → 立体声（索引 3 是环绕不是低音炮）")
    src50 = work / "src50.m4a"
    assert make_src(ff, src50, "5.0", channel_roles("5.0", 5), False), "5.0 素材生成失败"
    i50 = probe(env, src50)
    check("探测到 5.0 且声道数为 5",
          i50.audios[0].channels == 5, f"{i50.audios[0].channel_layout}")
    dst50 = work / "out50.wav"
    ok = run_steps(env, src50, dst50, DownmixSettings(container="wav"),
                   i50, "wav", "pcm_s16le", 0)
    check("5.0 下混成功", ok and dst50.exists())
    if ok:
        rate, chans = read_wav(dst50)
        comp = components(chans, rate, TOTAL)
        left, right = set(comp[0]), set(comp[1])
        # 5.0 的 c3=770Hz 是左后环绕（BL），绝不能被当成低音炮丢掉
        check("左声道混入 FL + FC + BL（c3 是环绕不是 LFE）",
              {"FL", "FC", "BL"} <= left, f"{sorted(left)}")
        check("右声道混入 FR + FC + BR", {"FR", "FC", "BR"} <= right,
              f"{sorted(right)}")

    # ---------------- 4. 夜间方案（响度 + 压缩） ---------------- #
    print("\n[4] 夜间方案（动态压缩 + 响度标准化）能跑通")
    night = DOWNMIX_PRESETS_BY_KEY["night"].settings
    dstn = work / "night.mp4"
    ok = run_steps(env, src, dstn, night, info, "mp4", "aac", 192)
    check("夜间方案执行成功", ok and dstn.exists())
    if ok:
        on = probe(env, dstn)
        check("夜间输出 2 声道且采样率正常",
              on.audios and on.audios[0].channels == 2
              and on.audios[0].sample_rate == 48000,
              f"{on.audios[0].channels}ch / {on.audios[0].sample_rate}Hz")

    # ---------------- 5. 对白方案 ---------------- #
    print("\n[5] 对白方案（中置加权 + 压缩）")
    dialog = DOWNMIX_PRESETS_BY_KEY["dialog"].settings
    dstd = work / "dialog.mp4"
    ok = run_steps(env, src, dstd, dialog, info, "mp4", "aac", 192)
    check("对白方案执行成功", ok and dstd.exists(), str(dstd.name))

    # ---------------- 6. 单声道 ---------------- #
    print("\n[6] 5.1 → 单声道")
    mono = DOWNMIX_PRESETS_BY_KEY["mono"].settings
    dstm = work / "mono.mp4"
    ok = run_steps(env, src, dstm, mono, info, "mp4", "aac", 128)
    check("单声道下混成功", ok and dstm.exists())
    if ok:
        om = probe(env, dstm)
        check("输出为 1 声道", om.audios and om.audios[0].channels == 1,
              f"{om.audios[0].channels}ch")

    # ---------------- 7. 源已是立体声 ---------------- #
    print("\n[7] 源已是立体声（不应报错，只做格式转换）")
    srcst = work / "stereo.m4a"
    assert make_src(ff, srcst, "stereo", ["FL", "FR"], False), "立体声素材生成失败"
    ist = probe(env, srcst)
    dstst = work / "out_stereo.mp3"
    ok = run_steps(env, srcst, dstst, DownmixSettings(container="mp3"),
                   ist, "mp3", "libmp3lame", 320)
    check("立体声源直接转码成功", ok and dstst.exists())
    if ok:
        ost = probe(env, dstst)
        check("输出仍为立体声", ost.audios and ost.audios[0].channels == 2)

    # ---------------- 5. 多轨合并：6 个单声道 → 1 条立体声 ---------------- #
    print("\n[5] 多轨合并：6 个单声道文件 → 只产出 1 条立体声")
    merge_dir = work / "merge"
    merge_dir.mkdir(exist_ok=True)
    chans = ["FL", "FR", "FC", "LFE", "BL", "BR"]
    srcs = []
    for role in chans:
        p = merge_dir / f"电影_{role}.wav"
        ok = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"aevalsrc=0.3*sin({ROLE_FREQ[role]}*2*PI*t):s=48000:c=mono",
             "-t", "2", str(p)], capture_output=True).returncode == 0
        check(f"生成 {p.name}", ok)
        srcs.append(p)

    roles = merge_roles("auto", 6)
    check("6 个文件解析成 5.1", roles == chans, str(roles))

    infos = [probe(env, p) for p in srcs]
    mdst = merge_dir / "电影.m4a"
    msteps = build_merge_downmix_steps(
        env, srcs, mdst, roles, std, audio_codec="aac", audio_bitrate_kbps=192,
        sample_rate=48000, durations=[i.duration for i in infos], container="m4a")
    check("构造出且只有一步", len(msteps) == 1)

    before = {p.name for p in merge_dir.iterdir()}
    r = subprocess.run(msteps[0].args, capture_output=True)
    ok = r.returncode == 0
    if not ok:
        print("    ffmpeg 报错:", r.stderr.decode("utf-8", "replace")[-400:])
    check("合并命令执行成功", ok)
    after = {p.name for p in merge_dir.iterdir()} - before
    check("6 个输入只产出 1 个文件", ok and len(after) == 1, str(after))
    if ok:
        out = probe(env, mdst)
        check("输出是立体声", out.audios and out.audios[0].channels == 2,
              f"{out.audios[0].channels}ch" if out.audios else "?")
        check("输出时长约 2s",
              abs(out.duration - 2.0) < 0.3, f"{out.duration:.2f}s")

        mw = merge_dir / "out_merge.wav"
        if extract_wav(ff, mdst, mw):
            rate, chans_out = read_wav(mw)
            hits = components(chans_out, rate, TOTAL, 0.04)
            print(f"      L 含 {hits[0]}\n      R 含 {hits[1]}")
            check("左声道含 FL/FC/BL", {"FL", "FC", "BL"} <= set(hits[0]),
                  str(hits[0]))
            check("右声道含 FR/FC/BR", {"FR", "FC", "BR"} <= set(hits[1]),
                  str(hits[1]))
            check("左声道没有 FR（未串声道）", "FR" not in hits[0], str(hits[0]))
            check("右声道没有 FL（未串声道）", "FL" not in hits[1], str(hits[1]))
            check("LFE 被丢弃", "LFE" not in hits[0] + hits[1],
                  str(hits[0] + hits[1]))

    # ---------------- 6. 多轨合并：时长不一致不丢内容 ---------------- #
    print("\n[6] 多轨合并：其中一路只有 0.5s，输出不能被截断")
    short_dir = work / "merge_short"
    short_dir.mkdir(exist_ok=True)
    srcs2 = []
    for i, role in enumerate(chans):
        p = short_dir / f"part_{role}.wav"
        secs = "0.5" if role == "LFE" else "2"
        subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"aevalsrc=0.3*sin({ROLE_FREQ[role]}*2*PI*t):s=48000:c=mono",
             "-t", secs, str(p)], capture_output=True)
        srcs2.append(p)
    infos2 = [probe(env, p) for p in srcs2]
    durs = [i.duration for i in infos2]
    check("探测到的时长确实不一致",
          max(durs) - min(durs) > 0.5, f"{min(durs):.2f}s ~ {max(durs):.2f}s")

    dst2 = short_dir / "out.wav"
    steps2 = build_merge_downmix_steps(
        env, srcs2, dst2, roles, std, audio_codec="pcm_s16le",
        audio_bitrate_kbps=0, sample_rate=48000, durations=durs, container="wav")
    cmd2 = steps2[0].display()
    check("短的 LFE 那路被补静音", cmd2.count("apad=whole_dur") == 1,
          f"apad ×{cmd2.count('apad')}")
    ok2 = subprocess.run(steps2[0].args, capture_output=True).returncode == 0
    check("执行成功", ok2)
    if ok2:
        with wave.open(str(dst2), "rb") as w:
            got = w.getnframes() / w.getframerate()
        check("输出取最长一路（≈2s，没有被 0.5s 截断）",
              1.8 < got < 2.3, f"{got:.2f}s")

    # ---------------- 7. 多轨合并：满幅素材不会削波 ---------------- #
    print("\n[7] 多轨合并：六路满幅叠加后靠限幅器兜住")
    loud_dir = work / "merge_loud"
    loud_dir.mkdir(exist_ok=True)
    loud_srcs = []
    for role in chans:
        p = loud_dir / f"L_{role}.wav"
        subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"aevalsrc=1.0*sin({ROLE_FREQ[role]}*2*PI*t):s=48000:c=mono",
             "-t", "1", str(p)], capture_output=True)
        loud_srcs.append(p)
    ldst = loud_dir / "out.wav"
    lsteps = build_merge_downmix_steps(
        env, loud_srcs, ldst, roles, std, audio_codec="pcm_s16le",
        audio_bitrate_kbps=0, sample_rate=48000, durations=[1.0] * 6,
        container="wav")
    ok3 = subprocess.run(lsteps[0].args, capture_output=True).returncode == 0
    check("执行成功", ok3)
    if ok3:
        rate3, c3 = read_wav(ldst)
        peak = max(max(abs(v) for v in ch) for ch in c3)
        flat = sum(1 for ch in c3 for v in ch if abs(v) >= 32766 / 32768) / max(
            sum(len(ch) for ch in c3), 1) * 100
        print(f"      峰值 {peak:.4f}，顶格样本 {flat:.2f}%")
        check("削波被限幅器挡住（峰值 ≤ 0.96）", peak <= 0.96, f"{peak:.4f}")
        check("没有顶格样本（硬削波会留下大片 1.0）", flat < 1.0, f"{flat:.2f}%")

    # ---------------- 8. 多轨合并任务装配 ---------------- #
    print("\n[8] 多轨合并：任务装配与输出命名")
    cfg = AppConfig()
    cfg.output_mode = "source"
    ctx = _FakeCtx(env, cfg)
    task = build_merge_downmix_task(ctx, srcs, infos, roles, std)
    check("只生成 1 个任务", task is not None)
    if task:
        check("输出名取共同词干 + _立体声",
              task.dst.name == "电影_立体声.m4a", task.dst.name)
        check("任务显示名点明合并了几路",
              task.name == "电影（6 路声道合并）", task.name)
        check("记录了全部 6 个来源文件",
              len(task.sources) == 6 and task.sources[0] == srcs[0])
        check("虚拟媒体信息带最长时长（供进度条用）",
              task.info is not None and abs(task.info.duration - 2.0) < 0.3,
              f"{task.info.duration:.2f}s" if task.info else "?")
        check("类别归到声道下混", task.kind_label == "声道下混")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  失败：{name}")
    print(f"测试目录：{work}")
    return 1 if FAIL else 0

if __name__ == "__main__":
    raise SystemExit(main())
