"""ffmpeg 命令构造。

统一产出「步骤列表」而不是单条命令，因为二次编码需要跑两遍，
目标体积模式也需要先算码率再跑两遍。
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg import FFmpegEnv, MediaInfo
from .presets import (AUTO_MERGE_LAYOUT, FORMAT_AUDIO_CODEC, HW_H264, HW_H265,
                      LOSSLESS_AUDIO_CODECS, ROLE_NAME, VIDEO_CONTAINERS,
                      Container, DownmixSettings, RateMode, VideoSettings,
                      resolve_codec)
from .utils import parse_bitrate


@dataclass
class CommandStep:
    """一个可独立执行的 ffmpeg 步骤。"""

    args: list[str]
    weight: float = 1.0
    label: str = ""

    def display(self) -> str:
        return " ".join(self.args)


def _base(env: FFmpegEnv) -> list[str]:
    return [env.ffmpeg or "ffmpeg", "-hide_banner", "-nostdin", "-y"]


def _tail() -> list[str]:
    return ["-progress", "pipe:1", "-nostats"]


# --------------------------------------------------------------------------- #
# 视频压缩
# --------------------------------------------------------------------------- #

def compute_video_bitrate_kbps(target_mb: float, duration: float,
                               audio_kbps: int) -> int:
    """按目标体积反推视频码率（kbps）。"""
    if duration <= 0 or target_mb <= 0:
        return 2000
    total_kbits = target_mb * 8 * 1024
    total_kbps = total_kbits / duration
    video_kbps = int(total_kbps - max(audio_kbps, 0))
    return max(video_kbps, 100)


def _scale_filter(s: VideoSettings, info: MediaInfo | None) -> str | None:
    if not s.scale_height:
        return None
    src_h = info.video.height if info and info.video else 0
    if src_h and src_h <= s.scale_height:
        return None  # 不放大
    # -2 保证宽高比不变且宽为偶数（H.264 要求）
    return f"scale=-2:{s.scale_height}:flags=lanczos"


# --- 硬件编码器的参数映射 --------------------------------------------------- #
# 各家硬件编码器对「速度档」和「恒定质量」的叫法完全不同，
# 直接套 libx264 的 -preset/-crf 会报错，所以必须分类处理。

_NVENC_PRESET = {
    "ultrafast": "p1", "superfast": "p1", "veryfast": "p2", "faster": "p3",
    "fast": "p3", "medium": "p4", "slow": "p5", "slower": "p6", "veryslow": "p7",
}

_AMF_QUALITY = {
    "ultrafast": "speed", "superfast": "speed", "veryfast": "speed",
    "faster": "speed", "fast": "speed", "medium": "balanced",
    "slow": "quality", "slower": "quality", "veryslow": "quality",
}


def _quality_args(codec: str, crf: float) -> list[str]:
    """把统一的『质量值』翻译成该编码器真正的恒定质量参数。

    统一区间 1.0–63.0（一位小数）。libx265 原生支持小数且上限 63；
    libx264 的 CRF 是整数、上限 51；NVENC 的 -cq 支持小数；
    AMF 的 qp 只接受整数。
    """
    crf = float(crf)
    if codec.endswith("_videotoolbox"):
        return ["-b:v", "0", "-q:v", str(_crf_to_qscale(crf))]
    if codec.endswith("_nvenc"):
        # NVENC 的 -cq 与 x264 的 -crf 数值区间接近，直接沿用（支持小数）
        cq = min(max(crf, 1.0), 51.0)
        return ["-rc", "vbr", "-cq", f"{cq:g}", "-b:v", "0"]
    if codec.endswith("_qsv"):
        return ["-global_quality", f"{crf:g}"]
    if codec.endswith("_amf"):
        q = int(round(min(max(crf, 2.0), 51.0)))
        return ["-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
    if codec == "libx264":
        # x264 的 CRF 只接受整数，上限 51
        return ["-crf", str(int(round(min(max(crf, 1.0), 51.0))))]
    # libx265 等：原生支持小数 CRF，范围 0–63
    return ["-crf", f"{crf:g}"]


def _speed_args(codec: str, preset: str) -> list[str]:
    """把统一的『速度档』翻译成该编码器支持的写法。"""
    if codec.endswith("_videotoolbox"):
        # VideoToolbox 只有「是否画质优先」这一个开关
        return ["-allow_sw", "1", "-prio_speed", "0"] if preset in (
            "slow", "slower", "veryslow", "medium") else ["-allow_sw", "1"]
    if codec.endswith("_nvenc"):
        return ["-preset", _NVENC_PRESET.get(preset, "p4")]
    if codec.endswith("_qsv"):
        return []                       # QSV 不接受 -preset
    if codec.endswith("_amf"):
        return ["-quality", _AMF_QUALITY.get(preset, "balanced")]
    return ["-preset", preset]


_HEVC_ENCODERS = {"libx265", "hevc_nvenc", "hevc_qsv", "hevc_amf",
                  "hevc_videotoolbox", "hevc_vaapi", HW_H265}


def _is_hw_like(codec: str) -> bool:
    """是否（可能是）硬件编码器 —— 体积估算时用，拿不到 env 也能判断。"""
    return codec in (HW_H264, HW_H265) or any(
        k in codec for k in ("nvenc", "qsv", "amf", "videotoolbox", "vaapi"))


def build_compress_steps(
    env: FFmpegEnv,
    src: Path,
    dst: Path,
    s: VideoSettings,
    info: MediaInfo | None = None,
    target_size_mb: float = 0.0,
    work_dir: Path | None = None,
) -> list[CommandStep]:
    steps: list[CommandStep] = []
    duration = info.duration if info else 0.0
    codec = resolve_codec(s.codec, env)     # auto_hw → 本机真正可用的硬件编码器
    copy_video = codec == "copy"

    # --- 码率模式决策 ---
    mode = s.rate_mode
    video_kbps = s.bitrate_kbps
    if mode == RateMode.TARGET_SIZE or (target_size_mb > 0 and mode != RateMode.CRF):
        if target_size_mb > 0:
            audio_kbps = 0 if s.audio_codec == "none" else (
                0 if s.audio_codec == "copy" else s.audio_bitrate_kbps)
            video_kbps = compute_video_bitrate_kbps(target_size_mb, duration, audio_kbps)
            mode = RateMode.TWOPASS

    # --- 视频编码参数 ---
    v_args: list[str] = []
    if copy_video:
        v_args += ["-c:v", "copy"]
    else:
        v_args += ["-c:v", codec]
        if codec in ("libx264", "libx265"):
            v_args += ["-preset", s.preset]
            if s.tune:
                v_args += ["-tune", s.tune]
            if s.profile:
                v_args += ["-profile:v", s.profile]
        else:
            v_args += _speed_args(codec, s.preset)

        if mode == RateMode.CRF:
            v_args += _quality_args(codec, s.crf)
        elif mode in (RateMode.BITRATE, RateMode.TWOPASS):
            v_args += ["-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.5)}k",
                       "-bufsize", f"{video_kbps * 2}k"]

        if s.pix_fmt:
            v_args += ["-pix_fmt", s.pix_fmt]
        if codec in _HEVC_ENCODERS and s.container == Container.MP4:
            v_args += ["-tag:v", "hvc1"]  # 否则 macOS 播放器不认

    # --- 缩放 / 帧率 ---
    filters: list[str] = []
    sc = _scale_filter(s, info)
    if sc:
        filters.append(sc)
    if s.fps:
        filters.append(f"fps={s.fps:g}")

    # --- 音频 ---
    a_args: list[str] = []
    if s.audio_codec == "none":
        a_args += ["-an"]
    elif s.audio_codec == "copy":
        a_args += ["-c:a", "copy"]
    else:
        a_args += ["-c:a", s.audio_codec, "-b:a", f"{s.audio_bitrate_kbps}k"]
        if s.audio_channels:
            a_args += ["-ac", str(s.audio_channels)]

    # --- 容器相关 ---
    m_args: list[str] = []
    if s.container == Container.WEBM:
        # WebM 只认 VP9/VP8 + Vorbis/Opus，直接覆盖上面算好的音视频参数
        v_args = ["-c:v", "libvpx-vp9", "-crf", str(s.crf), "-b:v", "0", "-row-mt", "1"]
        a_args = (["-an"] if s.audio_codec == "none"
                  else ["-c:a", "libopus", "-b:a", f"{s.audio_bitrate_kbps}k"])
    elif s.container == Container.MP4 and s.faststart:
        m_args += ["-movflags", "+faststart"]

    maps = ["-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0"]

    def _out_args(extra: list[str]) -> list[str]:
        args = _base(env) + ["-i", str(src)]
        args += maps
        args += v_args + a_args + m_args + s.extra_args
        if filters:
            args += ["-vf", ",".join(filters)]
        args += extra + _tail() + [str(dst)]
        return args

    if copy_video:
        steps.append(CommandStep(_out_args([]), 1.0, "仅换容器"))
        return steps

    if mode == RateMode.TWOPASS:
        wd = work_dir or Path(tempfile.mkdtemp(prefix="xiaowan_2pass_"))
        wd.mkdir(parents=True, exist_ok=True)
        log = wd / f"{dst.stem}_pass"
        null_out = "NUL" if _is_windows() else "/dev/null"
        p1 = _base(env) + ["-i", str(src)] + maps + v_args + [
            "-pass", "1", "-passlogfile", str(log), "-an"
        ]
        if filters:
            p1 += ["-vf", ",".join(filters)]
        p1 += ["-f", "null"] + _tail() + [null_out]
        steps.append(CommandStep(p1, 0.45, "第一遍分析"))
        p2 = _out_args(["-pass", "2", "-passlogfile", str(log)])
        steps.append(CommandStep(p2, 0.55, "第二遍编码"))
        return steps

    steps.append(CommandStep(_out_args([]), 1.0, "编码中"))
    return steps


def _is_windows() -> bool:
    import sys
    return sys.platform.startswith("win")


def _crf_to_qscale(crf: int) -> int:
    """把 CRF(0-51) 粗略映射到 VideoToolbox 的 q:v(1-100，越小越好)。"""
    crf = max(0, min(51, crf))
    return max(1, min(100, int(round(crf * 100 / 51))))


# --------------------------------------------------------------------------- #
# 音频提取
# --------------------------------------------------------------------------- #

def build_extract_audio_steps(
    env: FFmpegEnv,
    src: Path,
    dst: Path,
    codec: str,
    bitrate_kbps: int,
    sample_rate: int = 0,
    channels: int = 0,
    stream_index: int | None = None,
) -> list[CommandStep]:
    args = _base(env) + ["-i", str(src)]
    if stream_index is not None:
        args += ["-map", f"0:{stream_index}"]
    else:
        args += ["-map", "0:a:0?"]
    args += ["-vn"]

    if codec == "copy":
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", codec]
        if bitrate_kbps > 0 and codec not in ("flac", "pcm_s16le", "pcm_s24le"):
            args += ["-b:a", f"{bitrate_kbps}k"]
    if sample_rate:
        args += ["-ar", str(sample_rate)]
    if channels:
        args += ["-ac", str(channels)]
    if codec == "libmp3lame":
        args += ["-q:a", "0"] if bitrate_kbps <= 0 else []
    if codec == "libopus":
        args += ["-vbr", "on"]
    if codec.startswith("pcm"):
        args += ["-f", "wav"]

    args += _tail() + [str(dst)]
    return [CommandStep(args, 1.0, "提取音频")]


# --------------------------------------------------------------------------- #
# 封装 / 混流
# --------------------------------------------------------------------------- #

def build_remux_steps(
    env: FFmpegEnv,
    src: Path,
    dst: Path,
    container: Container,
    audio_mode: str = "all",       # all / first / none
    audio_codec: str = "copy",
    audio_bitrate_kbps: int = 192,
    subtitle_files: list[Path] | None = None,
    subtitle_langs: list[str] | None = None,
    faststart: bool = True,
    metadata: dict[str, str] | None = None,
) -> list[CommandStep]:
    args = _base(env) + ["-i", str(src)]
    sub_files = subtitle_files or []
    for sub in sub_files:
        args += ["-i", str(sub)]

    args += ["-map", "0:v:0"]
    if audio_mode == "all":
        args += ["-map", "0:a?"]
    elif audio_mode == "first":
        args += ["-map", "0:a:0?"]

    if container == Container.MKV:
        args += ["-map", "0:s?", "-map", "0:t?", "-map", "0:d?"]

    map_start = 1
    for i, _sub in enumerate(sub_files):
        args += ["-map", f"{map_start + i}:0"]

    args += ["-c:v", "copy"]

    if audio_codec == "copy" or audio_mode == "none":
        args += ["-c:a", "copy"] if audio_mode != "none" else ["-an"]
    else:
        args += ["-c:a", audio_codec, "-b:a", f"{audio_bitrate_kbps}k"]

    if sub_files:
        sub_codec = "srt" if container in (Container.MKV, Container.WEBM) else "mov_text"
        args += ["-c:s", sub_codec]
        for i, _sub in enumerate(sub_files):
            if subtitle_langs and i < len(subtitle_langs) and subtitle_langs[i]:
                args += [f"-metadata:s:s:{i}", f"language={subtitle_langs[i]}"]

    args += ["-map_metadata", "0"]
    for k, v in (metadata or {}).items():
        args += ["-metadata", f"{k}={v}"]

    if container == Container.MP4 and faststart:
        args += ["-movflags", "+faststart"]

    args += _tail() + [str(dst)]
    return [CommandStep(args, 1.0, "封装中")]


# --------------------------------------------------------------------------- #
# 体积估算
# --------------------------------------------------------------------------- #

def estimate_output_size_mb(
    s: VideoSettings,
    info: MediaInfo | None,
    target_size_mb: float = 0.0,
) -> float | None:
    """粗略估算输出体积，用于界面实时提示。"""
    if not info or info.duration <= 0:
        return None
    if s.codec == "copy":
        base = info.size / 1024 / 1024
        factor = 1.0
        if s.container == Container.MP4:
            factor = 0.99
        return base * factor

    if target_size_mb > 0 and s.rate_mode == RateMode.TARGET_SIZE:
        return target_size_mb

    audio_kbps = 0
    if s.audio_codec == "copy":
        if info.audios and info.audios[0].bit_rate:
            audio_kbps = info.audios[0].bit_rate / 1000
        else:
            audio_kbps = 192
    elif s.audio_codec != "none":
        audio_kbps = s.audio_bitrate_kbps

    ratio = 1.0
    src_h = info.video.height if info.video else 0
    if s.scale_height and src_h and src_h > s.scale_height:
        ratio = (s.scale_height / src_h) ** 2

    if s.rate_mode == RateMode.CRF:
        # CRF 只是质量目标，用源码率做启发式折算
        src_kbps = (info.bit_rate / 1000) if info.bit_rate else 0
        if not src_kbps and info.size and info.duration:
            src_kbps = info.size * 8 / info.duration / 1000
        if not src_kbps:
            return None
        # CRF 23 大致相当于源码率的 65%，每 +6 减半 / 每 -6 翻倍
        factor = 0.65 * (2 ** ((23 - s.crf) / 6.0))
        if s.codec in ("libx265", HW_H265) or "hevc" in s.codec:
            factor *= 0.65
        elif _is_hw_like(s.codec):
            # 硬件编码同画质下码率通常比软编高一截
            factor *= 1.25
        video_kbps = max(src_kbps * factor * ratio, 80)
    else:
        video_kbps = s.bitrate_kbps
        if target_size_mb > 0:
            video_kbps = compute_video_bitrate_kbps(
                target_size_mb, info.duration,
                0 if s.audio_codec in ("none", "copy") else s.audio_bitrate_kbps)

    total_kbps = video_kbps + audio_kbps
    return total_kbits_to_mb(total_kbps, info.duration)


def total_kbits_to_mb(kbps: float, seconds: float) -> float:
    return kbps * seconds / 8 / 1024


def cleanup_passlogs(work_dir: Path | None) -> None:
    if work_dir and work_dir.exists() and work_dir.name.startswith("xiaowan_2pass_"):
        shutil.rmtree(work_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 声道下混：5.1 / 7.1 → 立体声 / 单声道
# --------------------------------------------------------------------------- #
# 思路：用 `pan` 滤镜手写混音矩阵，而不是丢一个 -ac 2 让 ffmpeg 自己决定。
# 原因有二：
#   1. `-ac` 的下混系数由 swresample 内部决定，随版本变化，且无法调中置/环绕配比；
#   2. 观众真正在意的是「对白够不够清楚、爆炸会不会吓人」，
#      这需要能分别调中置、环绕、低音炮三路增益。
#
# 声道索引是这里唯一的硬约定。FFmpeg 各布局的排列（已在本机实测核对）：
#   stereo     FL FR
#   2.1        FL FR LFE
#   3.0        FL FR FC
#   quad       FL FR BL BR
#   5.0        FL FR FC BL BR          ← 注意：没有 LFE，索引 3 已经是左后环绕
#   5.1        FL FR FC LFE BL BR
#   5.1(side)  FL FR FC LFE SL SR
#   7.1        FL FR FC LFE BL BR SL SR
# 所以不能只看声道数，必须优先按 channel_layout 判断。

# 布局名 → 每个索引对应的声道角色
_LAYOUT_ROLES: dict[str, list[str]] = {
    "mono": ["FC"],
    "1.0": ["FC"],
    "stereo": ["FL", "FR"],
    "2.0": ["FL", "FR"],
    "2.1": ["FL", "FR", "LFE"],
    "3.0": ["FL", "FR", "FC"],
    "3.0(back)": ["FL", "FR", "BC"],
    "quad": ["FL", "FR", "BL", "BR"],
    "quad(side)": ["FL", "FR", "SL", "SR"],
    "4.0": ["FL", "FR", "FC", "BC"],
    "5.0": ["FL", "FR", "FC", "BL", "BR"],
    "5.0(side)": ["FL", "FR", "FC", "SL", "SR"],
    "5.1": ["FL", "FR", "FC", "LFE", "BL", "BR"],
    "5.1(side)": ["FL", "FR", "FC", "LFE", "SL", "SR"],
    "6.0": ["FL", "FR", "FC", "BC", "SL", "SR"],
    "6.1": ["FL", "FR", "FC", "LFE", "BC", "SL", "SR"],
    "7.0": ["FL", "FR", "FC", "BL", "BR", "SL", "SR"],
    "7.1": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
}

# 布局读不出来时，退而按声道数猜（取最常见的排列）
_ROLES_BY_COUNT: dict[int, list[str]] = {
    1: ["FC"],
    2: ["FL", "FR"],
    3: ["FL", "FR", "FC"],
    4: ["FL", "FR", "BL", "BR"],
    5: ["FL", "FR", "FC", "BL", "BR"],
    6: ["FL", "FR", "FC", "LFE", "BL", "BR"],
    7: ["FL", "FR", "FC", "LFE", "BC", "SL", "SR"],
    8: ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
}

_LEFT_ROLES = {"FL", "FLC", "BL", "SL", "BC"}
_RIGHT_ROLES = {"FR", "FRC", "BR", "SR", "BC"}


def channel_roles(layout: str, channels: int) -> list[str]:
    """返回每个声道索引对应的角色名（如 FL / FC / LFE / BL）。"""
    key = (layout or "").strip().lower()
    roles = _LAYOUT_ROLES.get(key)
    if roles and (not channels or len(roles) == channels):
        return list(roles)
    return list(_ROLES_BY_COUNT.get(channels, []))


def is_multichannel(layout: str, channels: int) -> bool:
    """是否属于「值得下混」的多声道素材（> 2 声道）。"""
    if channels > 2:
        return True
    roles = channel_roles(layout, channels)
    return len(roles) > 2


def layout_label(layout: str, channels: int) -> str:
    """给人看的声道描述。"""
    key = (layout or "").strip().lower()
    if key in ("mono", "1.0") or channels == 1:
        return "单声道 1.0"
    if key in ("stereo", "2.0") or channels == 2:
        return "立体声 2.0"
    name = layout or ((f"{channels}.1") if channels == 6 else f"{channels} 声道")
    return f"{name}（{channels} 声道）"


# --------------------------------------------------------------------------- #
# 多轨合并：N 个单声道文件 → 一条多声道
# --------------------------------------------------------------------------- #

def resolve_merge_layout(layout: str, count: int) -> str:
    """把「自动 / 用户指定」解析成一个确定的布局名；对不上文件数则返回空串。"""
    key = (layout or "auto").strip().lower()
    if key in ("", "auto"):
        key = AUTO_MERGE_LAYOUT.get(count, "")
    roles = _LAYOUT_ROLES.get(key)
    return key if roles and len(roles) == count else ""


def merge_roles(layout: str, count: int) -> list[str]:
    """合并时第 i 个文件应该装哪条声道。解析不出来返回空列表。"""
    return list(_LAYOUT_ROLES.get(resolve_merge_layout(layout, count), []))


def merge_layout_text(layout: str, count: int) -> str:
    """给界面用的一句话，如「5.1（6 路）：前左 → 前右 → 中置 → 低音炮 → 后左 → 后右」。"""
    roles = merge_roles(layout, count)
    if not roles:
        return ""
    names = " → ".join(ROLE_NAME.get(r, r) for r in roles)
    return f"{layout}（{count} 路）：{names}"


# 文件名里常见的声道写法 → 声道角色
_ROLE_ALIASES = {
    "fl": "FL", "l": "FL", "lt": "FL", "lf": "FL", "left": "FL",
    "fr": "FR", "r": "FR", "rt": "FR", "right": "FR",
    "fc": "FC", "c": "FC", "cn": "FC", "center": "FC", "centre": "FC", "mid": "FC",
    "lfe": "LFE", "sw": "LFE", "sub": "LFE", "subwoofer": "LFE",
    "bl": "BL", "lb": "BL", "rl": "BL", "rearleft": "BL",
    "br": "BR", "rb": "BR", "rr": "BR", "rearright": "BR",
    "sl": "SL", "ls": "SL", "sideleft": "SL", "surroundleft": "SL",
    "sr": "SR", "rs": "SR", "sideright": "SR", "surroundright": "SR",
}

_CN_ROLE = {
    "前左": "FL", "左前": "FL", "左": "FL", "左声道": "FL",
    "前右": "FR", "右前": "FR", "右": "FR", "右声道": "FR",
    "中置": "FC", "中": "FC", "中间": "FC",
    "低音炮": "LFE", "低音": "LFE", "重低音": "LFE", "低频": "LFE",
    "后左": "BL", "左后": "BL", "环绕左": "SL", "左环绕": "SL",
    "后右": "BR", "右后": "BR", "环绕右": "SR", "右环绕": "SR",
    "侧左": "SL", "左侧": "SL", "侧右": "SR", "右侧": "SR",
}

_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_EDGE_DIGITS = re.compile(r"^\d+|\d+$")


def guess_channel_role(stem: str, roles: list[str] | None = None) -> str:
    """从文件名猜它装的是哪条声道（猜不出返回空串）。

    声道标记一般落在文件名末尾（movie_FL.wav / 电影-前左.wav），
    所以从后往前找；顺手容忍 FL1 / 01FL 这类编号贴在一起的情况。

    传入 roles（合并布局的槽位表）可做语境校正：Pro Tools 等工具
    把后环绕导出成 Ls/Rs，而 5.1 布局的槽位是 BL/BR——布局里没有
    SL/SR 时，Ls/Rs 应该按后环绕算，否则永远对不上、没法自动排序。
    """
    tokens = [t for t in _TOKEN_SPLIT.split(stem or "") if t]
    for raw in reversed(tokens):
        tok = raw.lower()
        if tok in _ROLE_ALIASES or raw in _CN_ROLE:
            role = _ROLE_ALIASES.get(tok) or _CN_ROLE[raw]
            return _adapt_role_to_layout(role, roles)
        trimmed = _EDGE_DIGITS.sub("", tok)
        if trimmed and trimmed != tok:
            if trimmed in _ROLE_ALIASES:
                return _adapt_role_to_layout(_ROLE_ALIASES[trimmed], roles)
    return ""


def _adapt_role_to_layout(role: str, roles: list[str] | None) -> str:
    if not roles:
        return role
    # 环绕声道在「后置 BL/BR」与「侧置 SL/SR」两种写法间按布局互换
    if role == "SL" and "SL" not in roles and "BL" in roles:
        return "BL"
    if role == "SR" and "SR" not in roles and "BR" in roles:
        return "BR"
    if role == "BL" and "BL" not in roles and "SL" in roles:
        return "SL"
    if role == "BR" and "BR" not in roles and "SR" in roles:
        return "SR"
    return role


def merge_slot_order(stems: list[str], roles: list[str]) -> list[int] | None:
    """按文件名声道标记推算每个槽位应取的行号（reorder 用行号表）。

    判定不了（有文件认不出标记 / 标记重复 / 覆盖不了所有槽位）返回 None，
    调用方应放行、不强拦；能判定时返回行号表——等于 identity 即顺序正确，
    否则说明当前顺序与文件名不符（第 i 行应放第 order[i] 个文件）。
    """
    guessed = [guess_channel_role(s, roles) for s in stems]
    if any(not g for g in guessed):
        return None
    if len(set(guessed)) != len(guessed):
        return None
    if set(guessed) != set(roles):
        return None
    return [guessed.index(r) for r in roles]


def _db_to_linear(db: float) -> float:
    return float(10.0 ** (float(db) / 20.0))


def _pan_term(index: int, gain: float) -> str | None:
    if gain <= 0.0:
        return None
    if abs(gain - 1.0) < 1e-9:
        return f"c{index}"
    return f"{gain:.4g}*c{index}"


def build_downmix_matrix(roles: list[str], s: DownmixSettings) -> str | None:
    """按声道角色生成 pan 混音矩阵表达式，生成不出来时返回 None。"""
    if not roles:
        return None

    center = _db_to_linear(s.center_gain_db)
    surround = _db_to_linear(s.surround_gain_db)
    lfe = _db_to_linear(s.lfe_gain_db) if s.include_lfe else 0.0
    back_mid = surround * 0.7071          # 后中置：同时喂给左右两侧

    def gain_of(role: str, side: str) -> float:
        if role == "LFE":
            return lfe
        if role == "FC":
            return center
        if role in ("FLC", "FRC"):        # 左中/右中：各归一边，略降一点
            return center * 0.7071
        if role == "BC":                  # 后中置：两边都给
            return back_mid
        if role in ("FL", "FR"):          # 主声道原样保留
            if (role == "FL" and side == "L") or (role == "FR" and side == "R"):
                return 1.0
            return 0.0
        # 其余环绕类（BL / BR / SL / SR / TFL…）按环绕增益处理
        if side == "L" and role in _LEFT_ROLES:
            return surround
        if side == "R" and role in _RIGHT_ROLES:
            return surround
        return 0.0

    def build(side: str, scale: float = 1.0) -> list[str]:
        terms = []
        for i, role in enumerate(roles):
            term = _pan_term(i, gain_of(role, side) * scale)
            if term:
                terms.append(term)
        return terms

    if s.target_channels == 1:
        # 先把左右各自算出来，再对半相加（等价于 0.5*(L+R)），响度不会爆表
        left = build("L", 0.5)
        right = build("R", 0.5)
        merged: dict[str, float] = {}
        for expr in (left, right):
            for term in expr:
                coeff, _, idx = term.partition("*")
                if idx:
                    merged[idx] = merged.get(idx, 0.0) + float(coeff)
                else:
                    merged[coeff] = merged.get(coeff, 0.0) + 1.0
        # 按声道索引排一下序，生成的表达式读起来更顺
        ordered = sorted(merged.items(),
                         key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 99)
        mono = [_pan_term_from_key(k, v) for k, v in ordered]
        mono = [t for t in mono if t]
        if not mono:
            return None
        return "pan=mono|FC=" + "+".join(mono)

    left = build("L")
    right = build("R")
    if not left or not right:
        return None
    return ("pan=stereo|FL=" + "+".join(left)
            + "|FR=" + "+".join(right))


def _pan_term_from_key(key: str, gain: float) -> str | None:
    if gain <= 0:
        return None
    if abs(gain - 1.0) < 1e-9:
        return key
    return f"{gain:.4g}*{key}"


def build_downmix_filter(
    roles: list[str],
    s: DownmixSettings,
    src_channels: int = 0,
    target_rate: int = 0,
) -> str | None:
    """组装完整的音频滤镜链（不含 -ac）。没有可做的一步时返回 None。"""
    parts: list[str] = []

    if roles and src_channels > s.target_channels:
        matrix = build_downmix_matrix(roles, s)
        if matrix:
            parts.append(matrix)

    if s.dialog_compress:
        # 阈值压低、比率温和，只把忽大忽小的动态收一收，不做成广播味儿
        parts.append("acompressor=threshold=-20dB:ratio=3:attack=15:release=250:"
                     "makeup=2:level_sc=1")

    if s.normalize_loudness:
        lufs = float(s.loudness_lufs)
        parts.append(f"loudnorm=I={lufs:g}:TP=-1.5:LRA=11:linear=false")
        # loudnorm 内部固定跑 192kHz，必须显式拉回来，否则文件会莫名巨大
        parts.append(f"aresample={target_rate or 48000}")

    if s.prevent_clip:
        # 左右声道叠加后峰值可能过 1.0，硬削波是永久性失真，收在 -0.45dB 更稳。
        # level=disabled 不能省：alimiter 默认开启的自动增益会把结果重新推回
        # 0dB，等于把刚压下去的峰值又顶上去（实测满幅素材仍有 37% 顶格样本）。
        parts.append("alimiter=limit=0.95:level=disabled:attack=5:release=50")

    return ",".join(parts) or None


def resolve_downmix_format(container: str, src: Path,
                           info: MediaInfo | None) -> tuple[str, str, int]:
    """决定输出 (扩展名, 音频编码器, 默认码率)。"""
    fmt = (container or "auto").lower()
    if fmt != "auto":
        codec, br = FORMAT_AUDIO_CODEC.get(fmt, ("aac", 192))
        return fmt, codec, br

    has_video = bool(info and info.video)
    ext = src.suffix.lstrip(".").lower() or ("mp4" if has_video else "m4a")
    if has_video and ext not in VIDEO_CONTAINERS:
        ext = "mp4"
    codec, br = FORMAT_AUDIO_CODEC.get(ext, ("aac", 192))
    return ext, codec, br


def build_downmix_steps(
    env: FFmpegEnv,
    src: Path,
    dst: Path,
    s: DownmixSettings,
    info: MediaInfo | None = None,
    audio_codec: str = "aac",
    audio_bitrate_kbps: int = 192,
    stream_index: int | None = None,
    keep_video: bool = True,
    container: str = "mp4",
    faststart: bool = True,
) -> list[CommandStep]:
    """多声道下混。视频流直接复制，只重编码音频。"""
    args = _base(env) + ["-i", str(src)]

    has_video = bool(info and info.video) and keep_video \
        and container in VIDEO_CONTAINERS

    if has_video:
        args += ["-map", "0:v:0"]
    if stream_index is not None:
        args += ["-map", f"0:a:{stream_index}"]
    else:
        args += ["-map", "0:a:0?"]

    if has_video:
        args += ["-c:v", "copy"]
    else:
        args += ["-vn"]

    args += ["-c:a", audio_codec]
    if audio_bitrate_kbps > 0 and audio_codec not in LOSSLESS_AUDIO_CODECS:
        args += ["-b:a", f"{audio_bitrate_kbps}k"]
    if s.sample_rate:
        args += ["-ar", str(s.sample_rate)]
    if audio_codec == "libopus":
        args += ["-vbr", "on"]
    if audio_codec.startswith("pcm"):
        args += ["-f", "wav"]

    # --- 推断源声道信息 ---
    stream = None
    if info and info.audios:
        stream = info.audios[stream_index or 0] if (
            stream_index is not None and stream_index < len(info.audios)
        ) else info.audios[0]
    src_channels = stream.channels if stream else 0
    src_layout = getattr(stream, "channel_layout", "") if stream else ""
    src_rate = stream.sample_rate if stream else 0

    roles = channel_roles(src_layout, src_channels)
    target_rate = s.sample_rate or src_rate

    af = build_downmix_filter(roles, s, src_channels, target_rate)
    if af:
        args += ["-af", af]

    # pan 已经输出目标声道数；没有 pan 时用 -ac 兜底
    uses_pan = bool(roles) and src_channels > s.target_channels
    if not uses_pan and src_channels and src_channels != s.target_channels:
        args += ["-ac", str(s.target_channels)]

    args += ["-map_metadata", "0"]
    if container == "mp4" and faststart:
        args += ["-movflags", "+faststart"]

    args += _tail() + [str(dst)]
    label = "下混为立体声" if s.target_channels == 2 else "下混为单声道"
    return [CommandStep(args, 1.0, label)]


def build_merge_downmix_steps(
    env: FFmpegEnv,
    srcs: list[Path],
    dst: Path,
    roles: list[str],
    s: DownmixSettings,
    audio_codec: str = "aac",
    audio_bitrate_kbps: int = 192,
    sample_rate: int = 48000,
    durations: list[float] | None = None,
    container: str = "m4a",
) -> list[CommandStep]:
    """把 N 个「一路一条声道」的文件按 roles 顺序合成一条多声道，再下混成一条输出。

    合并用 amerge，不用 join —— 实测 join 的落位与它声明的 channel_layout
    对不上（左声道里跑进了右前的内容），amerge 的输出顺序则严格等于输入顺序。
    """
    count = len(srcs)
    if count == 0 or len(roles) != count:
        return []

    rate = int(sample_rate or 48000)
    durs = list(durations or [])
    pad_to = max(durs) if len(durs) == count and durs else 0.0
    # 各路时长不一致时必须补静音：否则 amerge 的结果会被最短的那一路截断
    needs_pad = bool(durs) and pad_to > 0 and (pad_to - min(durs)) > 0.05

    args = _base(env)
    for src in srcs:
        args += ["-i", str(src)]

    # 每路先规范成「同采样率 + 单声道」：采样率不一致 amerge 不认，
    # 而万一路里混进了立体声文件，多出来的声道会把后面的声道顺序挤歪。
    chains: list[str] = []
    for i in range(count):
        chain = f"[{i}:a]aformat=sample_rates={rate}:channel_layouts=mono"
        if needs_pad and i < len(durs) and durs[i] < pad_to:
            chain += f",apad=whole_dur={pad_to:.3f}"
        chains.append(f"{chain}[m{i}]")

    # 注意：输入标签必须和 amerge 写在同一条 filterchain 里，
    # 中间多一个分号会被当成「没有输入的滤镜」而报 Filter not found。
    fc = ";".join(chains) + ";" + "".join(f"[m{i}]" for i in range(count))
    fc += f"amerge=inputs={count}[mix]"

    af = build_downmix_filter(roles, s, src_channels=count, target_rate=rate)
    fc += ";[mix]" + (af if af else "anull") + "[out]"

    args += ["-filter_complex", fc, "-map", "[out]"]
    args += ["-c:a", audio_codec]
    if audio_bitrate_kbps > 0 and audio_codec not in LOSSLESS_AUDIO_CODECS:
        args += ["-b:a", f"{audio_bitrate_kbps}k"]
    if audio_codec == "libopus":
        args += ["-vbr", "on"]
    if audio_codec.startswith("pcm"):
        args += ["-f", "wav"]
    if container in ("mp4", "m4a", "mov"):
        args += ["-movflags", "+faststart"]

    args += _tail() + [str(dst)]
    return [CommandStep(args, 1.0, f"合成 {count} 路声道并下混")]


def downmix_preview(
    roles: list[str], s: DownmixSettings, src_channels: int = 0
) -> dict[str, str]:
    """给界面用：把各路的实际增益算成人话，方便用户判断当前配比。"""
    if not _db_to_linear(s.center_gain_db) and not roles:
        return {}
    out = {
        "中置（人声）": _gain_text(s.center_gain_db),
        "环绕（声场）": _gain_text(s.surround_gain_db),
        "低音炮 LFE": (_gain_text(s.lfe_gain_db) + " 混入") if s.include_lfe
                      else "丢弃",
    }
    return out


def _gain_text(db: float) -> str:
    if abs(db) < 0.05:
        return "0 dB 原样"
    return f"{db:+g} dB"
