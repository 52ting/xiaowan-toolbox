#!/usr/bin/env python3
"""核心链路端到端测试（不启动界面）。

直接调用 ffmpeg 跑通三条链路：
    1. 视频压缩（CRF / 二次编码 / 目标体积）
    2. 音频提取（MP3 / FLAC / 原样复制）
    3. 封装混流（MP4 -> MKV，含外挂字幕）

用法：
    python tests/smoke_test.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.commands import (build_compress_steps, build_extract_audio_steps,  # noqa: E402
                               build_remux_steps)
from app.core.ffmpeg import FFmpegEnv, probe  # noqa: E402
from app.core.presets import (Container, PRESETS_BY_KEY, RateMode,  # noqa: E402
                              VideoSettings)

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    mark = "✔" if condition else "✘"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))


def run_step(env: FFmpegEnv, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stderr or "")[-800:]


def make_sample(env: FFmpegEnv, path: Path, seconds: int = 4) -> None:
    subprocess.run([
        env.ffmpeg, "-hide_banner", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(path),
    ], capture_output=True)


def main() -> int:
    env = FFmpegEnv().refresh()
    print(f"ffmpeg : {env.ffmpeg}")
    print(f"ffprobe: {env.ffprobe or '（缺失，将走 ffmpeg 兜底解析）'}")
    print(f"version: {env.version}")
    if not env.ready:
        print("找不到 ffmpeg，无法测试")
        return 2

    work = Path(tempfile.mkdtemp(prefix="xiaowan_test_"))
    print(f"\n测试目录：{work}\n")

    # ---------------------------------------------------------------- #
    print("[0] 生成测试素材")
    src = work / "sample.mp4"
    make_sample(env, src)
    check("素材生成成功", src.exists() and src.stat().st_size > 0,
          f"{src.stat().st_size if src.exists() else 0} bytes")

    info = probe(env, src)
    check("探测到视频流", info.video is not None,
          f"{info.video.codec_name} {info.resolution} {info.fps:g}fps" if info.video else "无")
    check("探测到音频流", bool(info.audios),
          info.audios[0].codec_name if info.audios else "无")
    check("时长解析正确", 3.5 < info.duration < 4.6, f"{info.duration:.2f}s")

    # ---------------------------------------------------------------- #
    print("\n[1] 视频压缩")
    balanced = PRESETS_BY_KEY["balanced"]
    dst1 = work / "out_balanced.mp4"
    steps = build_compress_steps(env, src, dst1, balanced.video, info)
    code, err = run_step(env, steps[0].args)
    check("CRF 压缩成功", code == 0 and dst1.exists(),
          f"{dst1.stat().st_size if dst1.exists() else 0} bytes" if code == 0 else err[-200:])
    if dst1.exists():
        i2 = probe(env, dst1)
        check("输出可被正确解析", i2.ok, i2.summary)

    # 二次编码 / 目标体积
    dst2 = work / "out_target.mp4"
    settings = VideoSettings(codec="libx264", rate_mode=RateMode.TARGET_SIZE,
                             crf=23, audio_codec="aac", audio_bitrate_kbps=128)
    two_pass_dir = Path(tempfile.mkdtemp(prefix="xiaowan_2pass_"))
    steps = build_compress_steps(env, src, dst2, settings, info,
                                 target_size_mb=0.3, work_dir=two_pass_dir)
    check("目标体积模式生成两步", len(steps) == 2, f"{[s.label for s in steps]}")
    ok = True
    for st in steps:
        c, e = run_step(env, st.args)
        if c != 0:
            ok = False
            err = e
            break
    check("二次编码执行成功", ok and dst2.exists(),
          f"{dst2.stat().st_size / 1024:.0f} KB（目标 300 KB）" if ok else err[-200:])

    # 缩放 + 硬编/软编参数
    dst3 = work / "out_720.mp4"
    s3 = VideoSettings(codec="libx264", crf=26, preset="veryfast",
                       scale_height=180, audio_codec="none")
    steps = build_compress_steps(env, src, dst3, s3, info)
    code, err = run_step(env, steps[0].args)
    check("缩放压缩成功", code == 0 and dst3.exists(), err[-200:] if code else "")
    if dst3.exists():
        i3 = probe(env, dst3)
        check("缩放生效（高度 180）", i3.video is not None and i3.video.height == 180,
              i3.resolution)
        check("音轨已移除", not i3.audios)

    # ---------------------------------------------------------------- #
    print("\n[2] 音频提取")
    for ext, codec, br, name in [
        ("mp3", "libmp3lame", 192, "MP3 192k"),
        ("flac", "flac", 0, "FLAC 无损"),
        ("m4a", "aac", 128, "AAC 128k"),
        ("aac", "copy", 0, "原样复制"),
    ]:
        dst = work / f"audio.{ext}"
        steps = build_extract_audio_steps(env, src, dst, codec, br)
        code, err = run_step(env, steps[0].args)
        check(f"提取 {name}", code == 0 and dst.exists(),
              f"{dst.stat().st_size / 1024:.0f} KB" if dst.exists() else err[-160:])

    # ---------------------------------------------------------------- #
    print("\n[3] 封装混流")
    dst4 = work / "remux.mkv"
    steps = build_remux_steps(env, src, dst4, Container.MKV)
    code, err = run_step(env, steps[0].args)
    check("MP4 → MKV 换壳", code == 0 and dst4.exists(), err[-200:] if code else "")
    if dst4.exists():
        i4 = probe(env, dst4)
        check("换壳后信息完整", i4.ok and i4.video is not None,
              f"{i4.summary} · {i4.duration:.2f}s")

    # 外挂字幕
    sub = work / "sample.srt"
    sub.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕\n\n"
                   "2\n00:00:02,000 --> 00:00:04,000\n第二行\n", encoding="utf-8")
    dst5 = work / "remux_sub.mkv"
    steps = build_remux_steps(env, src, dst5, Container.MKV,
                              subtitle_files=[sub], subtitle_langs=["chi"])
    code, err = run_step(env, steps[0].args)
    check("MKV 嵌入字幕", code == 0 and dst5.exists(), err[-200:] if code else "")
    if dst5.exists():
        i5 = probe(env, dst5)
        check("字幕轨已写入", len(i5.subtitles) >= 1,
              f"{len(i5.subtitles)} 条字幕轨")

    # ---------------------------------------------------------------- #
    print("\n[4] 边界情况")
    bad = work / "broken.mp4"
    bad.write_bytes(b"this is not a video at all" * 10)
    bad_info = probe(env, bad)
    check("损坏文件被识别为无效", not bad_info.ok, bad_info.error or "无流信息")

    dst_bad = work / "should_fail.mp4"
    steps = build_compress_steps(env, bad, dst_bad, balanced.video, bad_info)
    code, _err = run_step(env, steps[0].args)
    check("损坏文件编码返回非 0", code != 0, f"exit={code}")

    from app.core.engine import friendly_error
    friendly = friendly_error(_err)
    check("错误信息已翻译成人话", friendly and "Error" not in friendly[:10], friendly[:60])

    # 目标体积换算
    from app.core.commands import compute_video_bitrate_kbps
    kbps = compute_video_bitrate_kbps(100, 600, 128)
    expect = (100 * 8 * 1024 / 600) - 128
    check("目标体积换算正确", abs(kbps - expect) < 2, f"{kbps} kbps（期望 {expect:.0f}）")

    # ---------------------------------------------------------------- #
    # Mac 上装了 brew ffmpeg 之后一定会有 ffprobe，这条路径必须单独验证
    print("\n[5] ffprobe JSON 解析（模拟 Mac 环境）")
    test_ffprobe_parsing(env, src)

    # ---------------------------------------------------------------- #
    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print(f"  失败：{name}")
    print(f"测试产物目录：{work}")
    return 1 if FAIL else 0


FFPROBE_SAMPLE = {
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264",
         "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
         "pix_fmt": "yuv420p", "duration": "10.5"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "channels": 2, "channel_layout": "stereo", "sample_rate": "48000",
         "bit_rate": "128000", "tags": {"language": "eng", "title": "English"}},
        {"index": 2, "codec_type": "audio", "codec_name": "ac3",
         "channels": 6, "channel_layout": "5.1", "sample_rate": "48000",
         "tags": {"language": "chi"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
         "tags": {"language": "chi"}},
    ],
    "format": {"duration": "10.5", "size": "12345678",
               "format_name": "matroska,webm", "bit_rate": "9000000"},
}


def test_ffprobe_parsing(env: FFmpegEnv, real_file: Path) -> None:
    import json as _json

    from app.core import ffmpeg as ff

    original_run = ff.subprocess.run

    class _FakeProc:
        def __init__(self, payload: bytes):
            self.stdout = payload
            self.stderr = b""

    payload = _json.dumps(FFPROBE_SAMPLE).encode("utf-8")
    ff.subprocess.run = lambda *a, **kw: _FakeProc(payload)
    try:
        fake_env = FFmpegEnv(ffmpeg=env.ffmpeg, ffprobe="/usr/local/bin/ffprobe")
        info = ff.probe_with_ffprobe(fake_env, real_file)
    finally:
        ff.subprocess.run = original_run

    check("ffprobe: 识别视频流", info.video is not None and info.video.width == 1920,
          info.resolution if info.video else "无")
    check("ffprobe: 分数帧率换算正确（30000/1001）",
          info.video is not None and abs(info.video.fps - 29.97) < 0.01,
          f"{info.video.fps:.3f}fps" if info.video else "无")
    check("ffprobe: 多音轨全部识别", len(info.audios) == 2,
          "、".join(f"{a.codec_name}/{a.channels}ch" for a in info.audios))
    check("ffprobe: 字幕轨识别", len(info.subtitles) == 1,
          info.subtitles[0].codec_name if info.subtitles else "无")
    check("ffprobe: 语言标签解析",
          info.audios[0].language == "eng" and info.audios[1].language == "chi",
          f"{info.audios[0].language} / {info.audios[1].language}")
    check("ffprobe: 标题标签解析", info.audios[0].title == "English",
          info.audios[0].title)
    check("ffprobe: 时长/体积/码率", abs(info.duration - 10.5) < 0.01
          and info.size == 12345678 and info.bit_rate == 9000000,
          f"{info.duration}s / {info.size}B / {info.bit_rate}bps")
    check("ffprobe: 概要文本", info.summary == "H264 · 1920×1080 · 29.97fps · AAC",
          info.summary)
    check("ffprobe: 声道布局解析（5.1 / stereo）",
          info.audios[1].channel_layout == "5.1"
          and info.audios[0].channel_layout == "stereo",
          f"{info.audios[1].channel_layout} / {info.audios[0].channel_layout}")
    check("ffprobe: 声道显示文本",
          info.audios[1].channel_text == "5.1"
          and info.audios[0].channel_text == "立体声",
          f"{info.audios[1].channel_text} / {info.audios[0].channel_text}")

    # has_probe 必须能挡住“把 ffmpeg 当 ffprobe”的误判
    bad_env = FFmpegEnv(ffmpeg=env.ffmpeg, ffprobe=env.ffmpeg)
    check("has_probe 防误判", not bad_env.has_probe, f"{Path(bad_env.ffprobe).name}")

    # 路径不存在时不应抛异常，而是走兜底
    missing = Path("C:/definitely/not/here/nope.mp4")
    fallback = ff.probe(FFmpegEnv(ffmpeg=env.ffmpeg), missing)
    check("缺失文件不崩溃", not fallback.ok, fallback.error or "无流信息")

    # 无格式时长时应回退到流时长
    no_dur = {"streams": [dict(FFPROBE_SAMPLE["streams"][0])], "format": {}}
    fake_env = FFmpegEnv(ffmpeg=env.ffmpeg, ffprobe="/usr/local/bin/ffprobe")
    original_run2 = ff.subprocess.run
    ff.subprocess.run = lambda *a, **kw: _FakeProc(
        _json.dumps(no_dur).encode("utf-8"))
    try:
        info2 = ff.probe_with_ffprobe(fake_env, real_file)
    finally:
        ff.subprocess.run = original_run2
    check("无格式时长时回退到流时长", abs(info2.duration - 10.5) < 0.01,
          f"{info2.duration}s")


if __name__ == "__main__":
    raise SystemExit(main())
