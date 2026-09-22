#!/usr/bin/env python3
"""声道下混的静态验证：布局解析 / 矩阵生成 / 命令构造。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.commands import (build_downmix_filter, build_downmix_matrix,
                               build_downmix_steps, build_merge_downmix_steps,
                               channel_roles, guess_channel_role,
                               is_multichannel, layout_label, merge_layout_text,
                               merge_roles, merge_slot_order,
                               resolve_downmix_format,
                               resolve_merge_layout)
from app.core.ffmpeg import FFmpegEnv, MediaInfo, StreamInfo
from app.core.pipeline import merge_output_stem
from app.core.presets import (DOWNMIX_PRESETS_BY_KEY, DownmixSettings,
                             estimate_audio_mb, format_size_mb)

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✔' if ok else '✘'} {name}" + (f"  —— {detail}" if detail else ""))


print("=" * 70)
print("[1] 声道布局解析")
cases = [
    ("stereo", 2, ["FL", "FR"]),
    ("5.1", 6, ["FL", "FR", "FC", "LFE", "BL", "BR"]),
    ("5.1(side)", 6, ["FL", "FR", "FC", "LFE", "SL", "SR"]),
    ("7.1", 8, ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"]),
    ("5.0", 5, ["FL", "FR", "FC", "BL", "BR"]),          # 注意：5.0 没有 LFE
    ("quad", 4, ["FL", "FR", "BL", "BR"]),
    ("", 5, ["FL", "FR", "FC", "BL", "BR"]),             # 布局未知，按声道数兜底
    ("", 6, ["FL", "FR", "FC", "LFE", "BL", "BR"]),
]
for layout, nch, want in cases:
    got = channel_roles(layout, nch)
    check(f"channel_roles({layout or '未知'!r}, {nch})", got == want, f"{got}")

check("5.0 被识别为多声道", is_multichannel("5.0", 5))
check("stereo 不算多声道", not is_multichannel("stereo", 2))
check("layout_label(5.1)", layout_label("5.1", 6) == "5.1（6 声道）",
      layout_label("5.1", 6))

print("\n[2] 矩阵生成")
roles51 = channel_roles("5.1", 6)
roles71 = channel_roles("7.1", 8)

std = build_downmix_matrix(roles51, DownmixSettings())
check("标准 5.1→2.0 矩阵", std ==
      "pan=stereo|FL=c0+0.7079*c2+0.7079*c4|FR=c1+0.7079*c2+0.7079*c5", str(std))

std71 = build_downmix_matrix(roles71, DownmixSettings())
check("标准 7.1→2.0 会带上侧环绕", std71 is not None and "c6" in std71 and "c7" in std71,
      str(std71))

mono = build_downmix_matrix(roles51, DownmixSettings(target_channels=1))
check("5.1→1.0 矩阵", mono is not None and mono.startswith("pan=mono|FC="), str(mono))

lfe_on = build_downmix_matrix(
    roles51, DownmixSettings(include_lfe=True, lfe_gain_db=-6.0))
check("低音炮混入时出现 c3", lfe_on is not None and "c3" in lfe_on, str(lfe_on))

dialog = DOWNMIX_PRESETS_BY_KEY["dialog"].settings
dm = build_downmix_matrix(roles51, dialog)
check("对白方案中置不衰减（FC 系数 1）",
      dm is not None and "+c2" in dm and "0.5" in dm, str(dm))

print("\n[3] 滤镜链组装")
plain = build_downmix_filter(roles51, DownmixSettings(), 6, 48000)
check("默认只加矩阵 + 限幅", plain is not None and "alimiter" in plain, str(plain))

night = DOWNMIX_PRESETS_BY_KEY["night"].settings
nf = build_downmix_filter(roles51, night, 6, 48000)
check("夜间方案含压缩 / 响度 / 重采样",
      all(k in (nf or "") for k in ("acompressor", "loudnorm", "aresample=48000")),
      str(nf))
check("loudnorm 后重采样回源采样率",
      build_downmix_filter(roles51, night, 6, 44100) is not None
      and "aresample=44100" in build_downmix_filter(roles51, night, 6, 44100))

stereo_src = build_downmix_filter(channel_roles("stereo", 2), DownmixSettings(), 2, 48000)
check("源已是立体声则不生成矩阵", stereo_src is not None and "pan=" not in stereo_src,
      str(stereo_src))

noclip = build_downmix_filter(
    roles51, DownmixSettings(prevent_clip=False), 6, 48000)
check("关掉防削波后没有 alimiter", noclip is not None and "alimiter" not in noclip,
      str(noclip))

print("\n[4] 命令构造")
env = FFmpegEnv(ffmpeg="ffmpeg")
info = MediaInfo(
    path=Path("movie.mkv"), duration=60.0, size=1000,
    video=StreamInfo(index=0, codec_type="video", codec_name="h264",
                     width=1920, height=1080),
    audios=[StreamInfo(index=1, codec_type="audio", codec_name="ac3",
                       channels=6, channel_layout="5.1", sample_rate=48000)],
)
steps = build_downmix_steps(
    env, Path("movie.mkv"), Path("out.mkv"),
    DOWNMIX_PRESETS_BY_KEY["standard"].settings, info,
    audio_codec="aac", audio_bitrate_kbps=192, container="mkv")
cmd = steps[0].display()
check("-c:v copy 保留画面", "-c:v copy" in cmd, cmd[:120])
check("映射了视频与音频", "-map 0:v:0" in cmd and "-map 0:a:0?" in cmd)
check("带上 pan 矩阵", "pan=stereo" in cmd)
check("没有多余的 -ac", "-ac 2" not in cmd)
check("label 正确", steps[0].label == "下混为立体声", steps[0].label)

audio_info = MediaInfo(
    path=Path("song.ac3"), duration=60.0,
    audios=[StreamInfo(index=0, codec_type="audio", codec_name="ac3",
                       channels=6, channel_layout="5.1", sample_rate=48000)],
)
steps2 = build_downmix_steps(
    env, Path("song.ac3"), Path("out.m4a"),
    DOWNMIX_PRESETS_BY_KEY["standard"].settings, audio_info,
    audio_codec="aac", audio_bitrate_kbps=192, container="m4a")
check("纯音频源走 -vn", "-vn" in steps2[0].display())

steps3 = build_downmix_steps(
    env, Path("song.ac3"), Path("out.mp3"),
    DownmixSettings(container="mp3", audio_bitrate_kbps=320), audio_info,
    audio_codec="libmp3lame", audio_bitrate_kbps=320, container="mp3")
check("mp3 命令带 libmp3lame", "libmp3lame" in steps3[0].display(),
      steps3[0].display()[:110])

print("\n[5] 输出格式推断")
for fmt, src, want in [
    ("auto", "movie.mp4", "mp4"), ("auto", "movie.mkv", "mkv"),
    ("auto", "song.ac3", "ac3"), ("mp3", "movie.mkv", "mp3"),
]:
    ext, codec, br = resolve_downmix_format(fmt, Path(src), None)
    check(f"resolve({fmt}, {src}) → {want}", ext == want, f"{ext} / {codec} / {br}")

print("\n[6] 多轨合并：布局解析")
for count, want in [(1, "mono"), (2, "stereo"), (4, "quad"), (5, "5.0"),
                    (6, "5.1"), (7, "6.1"), (8, "7.1")]:
    check(f"{count} 个文件 → {want}",
          resolve_merge_layout("auto", count) == want,
          resolve_merge_layout("auto", count))
check("指定的布局优先于自动推断",
      resolve_merge_layout("5.1(side)", 6) == "5.1(side)",
      resolve_merge_layout("5.1(side)", 6))
check("文件数与布局不符 → 解析失败",
      resolve_merge_layout("5.1", 4) == "", repr(resolve_merge_layout("5.1", 4)))
check("9 个文件没有对应布局",
      resolve_merge_layout("auto", 9) == "")
check("merge_roles(auto, 6)",
      merge_roles("auto", 6) == ["FL", "FR", "FC", "LFE", "BL", "BR"],
      str(merge_roles("auto", 6)))
check("merge_layout_text 带上中文声道名",
      merge_layout_text("5.1", 6) ==
      "5.1（6 路）：前左 → 前右 → 中置 → 低音炮 → 后左 → 后右",
      merge_layout_text("5.1", 6))

print("\n[7] 多轨合并：文件名认声道")
for name, want in [
    ("电影_FL.wav", "FL"), ("movie_FR.wav", "FR"), ("01_FC.wav", "FC"),
    ("movie.LFE.wav", "LFE"), ("后左.wav", "BL"), ("右后.wav", "BR"),
    ("中置.wav", "FC"), ("低音炮.wav", "LFE"), ("front_left.wav", "FL"),
    ("back_R.wav", "FR"), ("环绕左.wav", "SL"), ("movie-2026.wav", ""),
    ("声音素材.wav", ""), ("track1.wav", ""),
]:
    got = guess_channel_role(Path(name).stem)
    check(f"guess({name}) → {want or '认不出'}", got == want, got)

print("\n[7b] 文件名认声道：按布局语境校正")
_roles51 = ["FL", "FR", "FC", "LFE", "BL", "BR"]
_roles_side = ["FL", "FR", "FC", "LFE", "SL", "SR"]
check("5.1 布局下 Ls 认作后环绕 BL",
      guess_channel_role("movie.Ls", _roles51) == "BL",
      guess_channel_role("movie.Ls", _roles51))
check("5.1 布局下 Rs 认作后环绕 BR",
      guess_channel_role("movie.Rs", _roles51) == "BR",
      guess_channel_role("movie.Rs", _roles51))
check("5.1(side) 布局下 Ls 仍是侧环绕 SL",
      guess_channel_role("movie.Ls", _roles_side) == "SL",
      guess_channel_role("movie.Ls", _roles_side))
check("不带布局时 Ls 默认侧环绕 SL",
      guess_channel_role("movie.Ls") == "SL",
      guess_channel_role("movie.Ls"))
check("实战文件名：Ls.wav 在 5.1 布局 → BL",
      guess_channel_role("fzgtg 0702 5.1hunhe.Ls", _roles51) == "BL",
      guess_channel_role("fzgtg 0702 5.1hunhe.Ls", _roles51))

print("\n[7c] 多轨合并：开始前顺序校验")
_user_stems = ["fzgtg 0702 5.1hunhe." + s for s in
               ("L", "C", "LFE", "Ls", "R", "Rs")]
_check_order = merge_slot_order(_user_stems, _roles51)
check("用户实战错误顺序 L,C,LFE,Ls,R,Rs 可判定纠正表",
      _check_order == [0, 4, 1, 2, 3, 5], str(_check_order))
check("正确顺序返回 identity",
      merge_slot_order(["a_FL", "b_FR", "c_FC", "d_LFE", "e_BL", "f_BR"],
                       _roles51) == [0, 1, 2, 3, 4, 5])
check("有文件认不出标记 → 不强拦",
      merge_slot_order(["a_FL", "音乐", "c_FC", "d_LFE", "e_BL", "f_BR"],
                       _roles51) is None)
check("标记重复 → 不强拦",
      merge_slot_order(["a_FL", "b_FL", "c_FC", "d_LFE", "e_BL", "f_BR"],
                       _roles51) is None)
check("覆盖不了槽位（缺 LFE）→ 不强拦",
      merge_slot_order(["a_FL", "b_FR", "c_FC", "d_Ls", "e_BL", "f_BR"],
                       _roles51) is None)

print("\n[7d] 体积预估（解释「输出怎么这么小」）")
_dur = 90 * 60 + 5.4          # 用户那部 90 分钟的电影
check("192kbps AAC 立体声 90 分钟 ≈ 125MB",
      abs(estimate_audio_mb(_dur, "aac", 192) - 124.5) < 3.0,
      f"{estimate_audio_mb(_dur, 'aac', 192):.1f} MB")
check("320kbps AAC 约为 192kbps 的 1.67 倍",
      abs(estimate_audio_mb(_dur, "aac", 320) /
          estimate_audio_mb(_dur, "aac", 192) - 320 / 192) < 0.01)
check("WAV 16bit 立体声 48k ≈ 1.0GB",
      abs(estimate_audio_mb(_dur, "pcm_s16le", 0) - 1028) < 40,
      f"{estimate_audio_mb(_dur, 'pcm_s16le', 0):.0f} MB")
check("WAV 24bit 比 16bit 大 50%",
      abs(estimate_audio_mb(_dur, "pcm_s24le", 0) /
          estimate_audio_mb(_dur, "pcm_s16le", 0) - 1.5) < 0.01)
check("FLAC 明显小于 WAV",
      estimate_audio_mb(_dur, "flac", 0) < estimate_audio_mb(_dur, "pcm_s16le", 0) * 0.75)
check("无时长 → 0", estimate_audio_mb(0, "aac", 192) == 0.0)
check("体积文案：125 MB", format_size_mb(124.8) == "125 MB", format_size_mb(124.8))
check("体积文案：1.4 GB", format_size_mb(1433) == "1.4 GB", format_size_mb(1433))
check("体积文案：12.5 MB", format_size_mb(12.5) == "12.5 MB", format_size_mb(12.5))

print("\n[8] 多轨合并：输出命名")
check("剥掉声道后缀取共同词干",
      merge_output_stem([Path("D:/x/电影_FL.wav"), Path("D:/x/电影_FR.wav"),
                         Path("D:/x/电影_C.wav")]) == "电影",
      merge_output_stem([Path("D:/x/电影_FL.wav"), Path("D:/x/电影_FR.wav"),
                         Path("D:/x/电影_C.wav")]))
check("命名对不上时退回目录名",
      merge_output_stem([Path("D:/影片合辑/a.wav"),
                         Path("D:/影片合辑/b.wav")]) == "影片合辑",
      merge_output_stem([Path("D:/影片合辑/a.wav"), Path("D:/影片合辑/b.wav")]))

print("\n[9] 多轨合并：命令构造")
chans = [Path(f"D:/x/电影_{r}.wav") for r in ("FL", "FR", "FC", "LFE", "BL", "BR")]
msteps = build_merge_downmix_steps(
    env, chans, Path("D:/x/电影.m4a"), merge_roles("auto", 6),
    DownmixSettings(), audio_codec="aac", audio_bitrate_kbps=192,
    sample_rate=48000, durations=[60.0] * 6, container="m4a")
mcmd = msteps[0].display() if msteps else ""
check("6 个输入都喂了进去", mcmd.count("-i ") == 6, str(mcmd.count("-i ")))
check("用 amerge 合并（不用 join）", "amerge=inputs=6" in mcmd and "join=" not in mcmd)
check("每路规范成单声道同采样率",
      mcmd.count("channel_layouts=mono") == 6
      and mcmd.count("sample_rates=48000") == 6)
check("标签与 amerge 同段（中间不能有分号）",
      "[m5]amerge=inputs=6[mix]" in mcmd, "…" + mcmd[-260:][:140])
check("带上下混矩阵", "pan=stereo" in mcmd and "c0+0.7079*c2+0.7079*c4" in mcmd)
check("只映射 [out]", '-map [out]' in mcmd)
check("输出 AAC 192k", "-c:a aac" in mcmd and "-b:a 192k" in mcmd)
check("m4a 带 faststart", "+faststart" in mcmd)
check("没有视频流相关参数", "-c:v" not in mcmd)
check("时长一致时不补静音", "apad" not in mcmd)
check("label 说明合并了几路",
      msteps and msteps[0].label == "合成 6 路声道并下混",
      msteps[0].label if msteps else "")

msteps2 = build_merge_downmix_steps(
    env, chans, Path("D:/x/电影.m4a"), merge_roles("auto", 6),
    DownmixSettings(), audio_codec="aac", audio_bitrate_kbps=192,
    sample_rate=48000, durations=[60.0, 60.0, 60.0, 12.0, 60.0, 60.0],
    container="m4a")
mcmd2 = msteps2[0].display()
check("时长不一致时给短的那路补静音",
      "apad=whole_dur=60.000" in mcmd2 and mcmd2.count("apad") == 1,
      f"apad ×{mcmd2.count('apad')}")
check("时长一致的路不补", mcmd2.count("apad=whole_dur") == 1)

msteps3 = build_merge_downmix_steps(
    env, chans[:6], Path("D:/x/电影.m4a"), merge_roles("auto", 6),
    DOWNMIX_PRESETS_BY_KEY["night"].settings, audio_codec="aac",
    audio_bitrate_kbps=192, sample_rate=48000, durations=[60.0] * 6,
    container="m4a")
mcmd3 = msteps3[0].display()
check("合并路径也吃预设（压缩/响度/限幅）",
      all(k in mcmd3 for k in ("acompressor", "loudnorm", "alimiter")), "")
check("限幅器关掉自动增益（否则等于没限）", "level=disabled" in mcmd3)

msteps4 = build_merge_downmix_steps(
    env, chans, Path("D:/x/out.wav"), merge_roles("auto", 6),
    DownmixSettings(target_channels=1), audio_codec="pcm_s16le",
    audio_bitrate_kbps=0, sample_rate=48000, durations=[60.0] * 6,
    container="wav")
mcmd4 = msteps4[0].display()
check("可塌成单声道", "pan=mono" in mcmd4, "")
check("无损输出不带 -b:a", "-b:a" not in mcmd4)
check("wav 带 -f wav", "-f wav" in mcmd4)

check("声道数与角色数对不上时拒绝构造",
      build_merge_downmix_steps(env, chans, Path("D:/x/o.m4a"),
                                merge_roles("auto", 4), DownmixSettings()) == [])

print("\n" + "=" * 70)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name in FAIL:
    print(f"  失败：{name}")
raise SystemExit(1 if FAIL else 0)
