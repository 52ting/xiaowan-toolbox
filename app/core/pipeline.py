"""把界面上的参数装配成一个可执行的 Task。"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .commands import (build_compress_steps, build_downmix_steps,
                       build_extract_audio_steps, build_merge_downmix_steps,
                       build_remux_steps, resolve_downmix_format)
from .ffmpeg import MediaInfo, StreamInfo
from .presets import (LOSSLESS_AUDIO_CODECS, VIDEO_CONTAINERS, Container,
                      DownmixSettings, VideoSettings)
from .task import Task, TaskKind
from .utils import safe_stem, unique_path


def make_output_path(cfg, src: Path, ext: str, suffix: str = "",
                     force_suffix: bool = False,
                     stem_override: str | None = None) -> Path:
    """按配置推导输出路径，必要时自动改名避免覆盖。

    stem_override 用于「多文件合并」：输出名不该沿用某一个输入文件的名字。
    """
    outdir = cfg.resolved_output_dir(src)
    stem = safe_stem(Path(stem_override)) if stem_override else safe_stem(src)
    if suffix or force_suffix:
        stem = f"{stem}{suffix}"
    target = outdir / f"{stem}.{ext}"
    try:
        same_as_source = target.resolve() == src.resolve()
    except OSError:
        same_as_source = False
    if same_as_source or (not cfg.overwrite and target.exists()):
        target = unique_path(target)
    return target


# --------------------------------------------------------------------------- #
# 视频压缩
# --------------------------------------------------------------------------- #

def build_compress_task(ctx, src: Path, info: MediaInfo | None,
                        settings: VideoSettings, target_size_mb: float = 0.0,
                        suffix: str | None = None) -> Task:
    cfg = ctx.config
    if suffix is None:
        suffix = cfg.filename_suffix
    dst = make_output_path(cfg, src, settings.container.value, suffix)

    work_dir: Path | None = None
    from .presets import RateMode
    if settings.rate_mode in (RateMode.TWOPASS, RateMode.TARGET_SIZE) or target_size_mb > 0:
        work_dir = Path(tempfile.mkdtemp(prefix="xiaowan_2pass_"))

    steps = build_compress_steps(
        ctx.env, src, dst, settings, info,
        target_size_mb=target_size_mb, work_dir=work_dir,
    )
    return Task(kind=TaskKind.COMPRESS, src=src, dst=dst, steps=steps,
                info=info, work_dir=work_dir)


# --------------------------------------------------------------------------- #
# 音频提取
# --------------------------------------------------------------------------- #

def build_audio_task(ctx, src: Path, info: MediaInfo | None, ext: str,
                     codec: str, bitrate_kbps: int, sample_rate: int = 0,
                     channels: int = 0, suffix: str | None = None) -> Task:
    cfg = ctx.config
    if suffix is None:
        suffix = cfg.filename_suffix
    dst = make_output_path(cfg, src, ext, suffix)
    steps = build_extract_audio_steps(
        ctx.env, src, dst, codec, bitrate_kbps, sample_rate, channels)
    return Task(kind=TaskKind.AUDIO, src=src, dst=dst, steps=steps, info=info)


# --------------------------------------------------------------------------- #
# 封装混流
# --------------------------------------------------------------------------- #

def build_remux_task(ctx, src: Path, info: MediaInfo | None,
                     container: Container, audio_mode: str = "all",
                     audio_codec: str = "copy", audio_bitrate_kbps: int = 192,
                     embed_subtitles: bool = False, faststart: bool = True,
                     suffix: str | None = None) -> Task:
    cfg = ctx.config
    if suffix is None:
        suffix = cfg.filename_suffix
    dst = make_output_path(cfg, src, container.value, suffix)

    sub_files: list[Path] = []
    sub_langs: list[str] = []
    if embed_subtitles:
        from .ffmpeg import find_subtitles
        from .utils import SUB_EXTS
        for cand in find_subtitles(src):
            sub_files.append(cand)
            sub_langs.append(_guess_lang(cand))
        if not sub_files and info:
            for i, st in enumerate(info.subtitles):
                sub_langs.append(st.language or "")

    steps = build_remux_steps(
        ctx.env, src, dst, container,
        audio_mode=audio_mode, audio_codec=audio_codec,
        audio_bitrate_kbps=audio_bitrate_kbps,
        subtitle_files=sub_files, subtitle_langs=sub_langs,
        faststart=faststart,
    )
    return Task(kind=TaskKind.REMUX, src=src, dst=dst, steps=steps, info=info)


def _guess_lang(path: Path) -> str:
    """从文件名尾巴猜语言，如 xxx.chs.srt -> chi"""
    stem = path.stem.lower()
    mapping = {
        "chs": "chi", "cht": "chi", "chi": "chi", "zh": "chi", "sc": "chi", "tc": "chi",
        "eng": "eng", "en": "eng", "jpn": "jpn", "jp": "jpn", "ja": "jpn",
        "kor": "kor", "kr": "kor",
    }
    token = stem.replace("_", ".").replace("-", ".").split(".")[-1]
    return mapping.get(token, "")


# --------------------------------------------------------------------------- #
# 声道下混（5.1 / 7.1 → 立体声 / 单声道）
# --------------------------------------------------------------------------- #

def build_downmix_task(ctx, src: Path, info: MediaInfo | None,
                       settings: DownmixSettings,
                       stream_index: int | None = None,
                       suffix: str | None = None) -> Task:
    cfg = ctx.config
    ext, codec, default_br = resolve_downmix_format(settings.container, src, info)

    if suffix is None:
        suffix = cfg.filename_suffix or (
            "_立体声" if settings.target_channels == 2 else "_单声道")
    dst = make_output_path(cfg, src, ext, suffix)

    has_video = bool(info and info.video) and ext in VIDEO_CONTAINERS
    bitrate = 0 if codec in LOSSLESS_AUDIO_CODECS else int(
        settings.audio_bitrate_kbps or default_br or 192)

    steps = build_downmix_steps(
        ctx.env, src, dst, settings, info,
        audio_codec=codec, audio_bitrate_kbps=bitrate,
        stream_index=stream_index, keep_video=has_video,
        container=ext, faststart=True,
    )
    return Task(kind=TaskKind.DOWNMIX, src=src, dst=dst, steps=steps, info=info)


# --------------------------------------------------------------------------- #
# 多轨合并下混：N 个单声道文件 → 一条立体声
# --------------------------------------------------------------------------- #

_CHANNEL_TAIL = re.compile(
    r"[ _\-.]*(\d*)(fl|fr|fc|lfe|sl|sr|bl|br|ls|rs|l|r|c|sw)[ _\-.]*$", re.I)


def merge_output_stem(paths: list[Path]) -> str:
    """给合并输出挑个名字。

    movie_FL.wav / movie_FR.wav … 剥掉尾部的声道标记后是同一个名字，
    就直接用它；名字对不上（各文件命名很乱）就退回目录名。
    """
    if not paths:
        return "output"

    def strip(stem: str) -> str:
        out, prev = stem, None
        while out and out != prev:
            prev = out
            out = _CHANNEL_TAIL.sub("", out)
        return out.strip(" _-.")

    stems = [strip(p.stem) for p in paths]
    cleaned = [s for s in stems if s]
    if cleaned and len({s.lower() for s in cleaned}) == 1:
        return cleaned[0]

    parents = {str(p.parent.resolve()) for p in paths}
    if len(parents) == 1:
        folder = paths[0].parent.name
        if folder:
            return folder
    return cleaned[0] if cleaned else paths[0].stem


def build_merge_downmix_task(
    ctx,
    srcs: list[Path],
    infos: list[MediaInfo | None],
    roles: list[str],
    settings: DownmixSettings,
    suffix: str | None = None,
) -> Task | None:
    """把多个单声道文件合成一条多声道再下混，只产出一个文件。"""
    if len(srcs) < 1 or len(roles) != len(srcs):
        return None

    cfg = ctx.config
    first = srcs[0]
    ext, codec, default_br = resolve_downmix_format(settings.container, first, None)
    # 「自动」跟随源文件在这条路径上没意义（源是一堆单声道文件，
    # 而且可能扩展名各不相同），统一落到 AAC/M4A。
    if (settings.container or "auto").lower() == "auto":
        ext, codec, default_br = "m4a", "aac", 256

    if suffix is None:
        suffix = cfg.filename_suffix or (
            "_立体声" if settings.target_channels == 2 else "_单声道")
    stem = merge_output_stem(srcs)
    dst = make_output_path(cfg, first, ext, suffix, stem_override=stem)

    durations = [info.duration if info else 0.0 for info in infos]
    known = [d for d in durations if d > 0]
    duration = max(known) if known else 0.0

    rates = [info.audios[0].sample_rate for info in infos
             if info and info.audios and info.audios[0].sample_rate]
    rate = int(settings.sample_rate or (max(rates) if rates else 48000))

    bitrate = 0 if codec in LOSSLESS_AUDIO_CODECS else int(
        settings.audio_bitrate_kbps or default_br or 192)

    steps = build_merge_downmix_steps(
        ctx.env, srcs, dst, roles, settings,
        audio_codec=codec, audio_bitrate_kbps=bitrate,
        sample_rate=rate, durations=durations, container=ext,
    )
    if not steps:
        return None

    # 造一个「合成后」的媒体信息：队列靠它的时长算进度
    merged = MediaInfo(
        path=dst, duration=duration, size=sum(
            (info.size if info else 0) for info in infos),
        audios=[StreamInfo(index=0, codec_type="audio", codec_name=codec,
                           channels=len(srcs), sample_rate=rate)],
    )
    label = f"{stem}（{len(srcs)} 路声道合并）"
    return Task(kind=TaskKind.DOWNMIX, src=first, dst=dst, steps=steps,
                info=merged, label=label, sources=list(srcs))
