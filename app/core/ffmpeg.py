"""ffmpeg / ffprobe 定位、媒体探测、字幕探测。

设计要点：Mac 上 ffmpeg 来源很杂（Homebrew / MacPorts / 官方静态包 / 内置），
所以这里做多级回退，并且允许用户在设置页手动指定。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .utils import SUB_EXTS, is_macos, is_windows

CREATE_NO_WINDOW = 0x08000000 if is_windows() else 0


class FFmpegError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 二进制定位
# --------------------------------------------------------------------------- #

_MAC_SEARCH_DIRS = [
    "/opt/homebrew/bin",          # Apple Silicon Homebrew
    "/usr/local/bin",             # Intel Homebrew / 手动安装
    "/opt/local/bin",             # MacPorts
    "/usr/bin",
]


def _bundled_dir() -> Path:
    """打包进 .app / .exe 的 bin 目录（PyInstaller 解包后会落到 _MEIPASS）。

    注意：Windows 下的文件名带 .exe 后缀，所以两种命名都要认。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = Path(base) / "bin"
        if cand.is_dir():
            return cand
    # 兼容打包后 exe 同级目录手工放置的 bin/
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    here = Path(__file__).resolve()
    roots = [p for p in ([exe_dir] if exe_dir else []) + list(here.parents) if p]
    for parent in roots:
        cand = parent / "bin"
        if not cand.is_dir():
            continue
        if (cand / "ffmpeg").is_file() or (cand / "ffmpeg.exe").is_file():
            return cand
    return Path("/nonexistent")


def _candidates(name: str, user_path: str | None = None) -> list[Path]:
    exe = f"{name}.exe" if is_windows() else name
    out: list[Path] = []

    if user_path:
        out.append(Path(user_path))

    env = os.environ.get(f"XIAOWAN_{name.upper()}")
    if env:
        out.append(Path(env))

    out.append(_bundled_dir() / exe)

    which = shutil.which(name)
    if which:
        out.append(Path(which))

    if is_macos():
        for d in _MAC_SEARCH_DIRS:
            out.append(Path(d) / exe)

    # 开发期便利：imageio-ffmpeg 自带的静态 ffmpeg（注意：它不含 ffprobe，
    # 所以这个兜底只能用于 ffmpeg 本体，否则会把 ffmpeg 误当成 ffprobe）
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore

            out.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
        except Exception:
            pass

    return out


def find_binary(name: str, user_path: str | None = None) -> str | None:
    """返回可用的可执行文件路径，找不到返回 None。"""
    seen: set[str] = set()
    for cand in _candidates(name, user_path):
        try:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return None


@dataclass
class FFmpegEnv:
    """当前生效的 ffmpeg 环境。"""

    ffmpeg: str | None = None
    ffprobe: str | None = None
    version: str = ""
    user_ffmpeg: str = ""
    _encoders: list[str] = field(default_factory=list, repr=False)
    _usable: dict[str, bool] = field(default_factory=dict, repr=False)

    @property
    def ready(self) -> bool:
        return bool(self.ffmpeg)

    @property
    def has_probe(self) -> bool:
        # 兜底校验：路径里必须真的含 "ffprobe"，防止把 ffmpeg 本体误当成 ffprobe
        return bool(self.ffprobe) and "ffprobe" in Path(self.ffprobe).name.lower()

    def refresh(self, user_ffmpeg: str = "", user_ffprobe: str = "") -> "FFmpegEnv":
        self.user_ffmpeg = user_ffmpeg
        self._encoders = []
        self._usable = {}
        self.ffmpeg = find_binary("ffmpeg", user_ffmpeg)

        # ffprobe 优先找与 ffmpeg 同目录的那一个，再退回全局搜索
        probe_exe = "ffprobe.exe" if is_windows() else "ffprobe"
        self.ffprobe = None
        if self.ffmpeg:
            sibling = Path(self.ffmpeg).with_name(probe_exe)
            if sibling.is_file() and os.access(sibling, os.X_OK):
                self.ffprobe = str(sibling)
        if not self.ffprobe:
            self.ffprobe = find_binary("ffprobe", user_ffprobe)

        self.version = ""
        if self.ffmpeg:
            try:
                res = subprocess.run(
                    [self.ffmpeg, "-version"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=CREATE_NO_WINDOW,
                )
                first = (res.stdout or res.stderr or "").splitlines()
                if first:
                    self.version = first[0].strip()
            except Exception:
                pass
        return self

    def encoder_list(self) -> list[str]:
        """ffmpeg 编译进来的编码器名字列表（只查一次，之后的调用走缓存）。"""
        if not self._encoders:
            names: list[str] = []
            if self.ffmpeg:
                try:
                    res = subprocess.run(
                        [self.ffmpeg, "-hide_banner", "-encoders"],
                        capture_output=True, text=True, timeout=20,
                        creationflags=CREATE_NO_WINDOW,
                    )
                    for line in (res.stdout or "").splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] and parts[0][0] in "VAS":
                            names.append(parts[1])
                except Exception:
                    pass
            self._encoders = names
        return self._encoders

    def encoder_available(self, encoder: str) -> bool:
        """ffmpeg 是否编译了该编码器。"""
        return bool(self.ffmpeg) and encoder in self.encoder_list()

    def encoder_usable(self, encoder: str, extra: list[str] | None = None) -> bool:
        """真机试编 1 帧，确认硬件编码器真的能用。

        「编译进去了」和「本机能跑」是两回事：h264_qsv / h264_amf 常年出现在
        -encoders 列表里，但没有对应显卡或驱动时一跑就报错。所以这里必须实测。

        结果按编码器缓存，避免反复试编拖慢界面。
        """
        key = encoder if not extra else f"{encoder}|{' '.join(extra)}"
        if key in self._usable:
            return self._usable[key]
        ok = False
        if self.encoder_available(encoder):
            try:
                res = subprocess.run(
                    [self.ffmpeg or "ffmpeg", "-hide_banner", "-loglevel", "error",
                     "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=192x108:r=25",
                     "-frames:v", "3", "-an", "-c:v", encoder, *(extra or []),
                     "-f", "null", "-"],
                    capture_output=True, timeout=12,
                    creationflags=CREATE_NO_WINDOW,
                )
                ok = res.returncode == 0 and not (res.stderr or b"").strip()
            except Exception:
                ok = False
        self._usable[key] = ok
        return ok


# --------------------------------------------------------------------------- #
# 媒体探测
# --------------------------------------------------------------------------- #

@dataclass
class StreamInfo:
    index: int = 0
    codec_type: str = ""
    codec_name: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bit_rate: int = 0
    channels: int = 0
    channel_layout: str = ""      # stereo / 5.1 / 5.1(side) / 7.1 …
    sample_rate: int = 0
    pix_fmt: str = ""
    language: str = ""
    title: str = ""
    duration: float = 0.0

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "—"

    @property
    def channel_text(self) -> str:
        """声道的人话描述，优先用布局名。"""
        lay = self.channel_layout.strip().lower()
        if lay in ("mono", "1.0") or self.channels == 1:
            return "单声道"
        if lay in ("stereo", "2.0") or self.channels == 2:
            return "立体声"
        if lay:
            return lay
        return f"{self.channels}ch" if self.channels else ""

    @property
    def label(self) -> str:
        parts = [f"#{self.index}", self.codec_name or "?"]
        if self.codec_type == "video":
            parts.append(self.resolution)
            if self.fps:
                parts.append(f"{self.fps:g}fps")
        elif self.codec_type == "audio":
            ch = self.channel_text
            if ch:
                parts.append(ch)
            if self.sample_rate:
                parts.append(f"{self.sample_rate / 1000:g}kHz")
        if self.language:
            parts.append(self.language)
        if self.title:
            parts.append(self.title)
        return " · ".join(parts)


@dataclass
class MediaInfo:
    path: Path
    duration: float = 0.0
    size: int = 0
    format_name: str = ""
    bit_rate: int = 0
    video: StreamInfo | None = None
    audios: list[StreamInfo] = field(default_factory=list)
    subtitles: list[StreamInfo] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and (self.video is not None or bool(self.audios))

    @property
    def is_video(self) -> bool:
        return self.video is not None

    @property
    def resolution(self) -> str:
        return self.video.resolution if self.video else "—"

    @property
    def fps(self) -> float:
        return self.video.fps if self.video else 0.0

    @property
    def summary(self) -> str:
        if self.error:
            return self.error
        if self.video:
            bits = [self.video.codec_name.upper(), self.video.resolution]
            if self.video.fps:
                bits.append(f"{self.video.fps:g}fps")
            if self.audios:
                a = self.audios[0]
                audio = a.codec_name.upper()
                lay = a.channel_layout.strip().lower()
                # 立体声是默认形态不用标；5.1 / 7.1 这类多声道必须亮出来
                if lay and lay not in ("stereo", "2.0", "mono", "1.0"):
                    audio += f" · {lay}"
                elif a.channels > 2:
                    audio += f" · {a.channels}ch"
                bits.append(audio)
            return " · ".join(bits)
        if self.audios:
            a = self.audios[0]
            bits = [a.codec_name.upper()]
            ch = a.channel_text
            if ch:
                bits.append(ch)
            if a.sample_rate:
                bits.append(f"{a.sample_rate / 1000:g}kHz")
            return " · ".join(bits)
        return "未知"


def _parse_fps(rate: str | None) -> float:
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def probe_with_ffprobe(env: FFmpegEnv, path: Path) -> MediaInfo:
    cmd = [
        env.ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-i", str(path),
    ]
    res = subprocess.run(
        cmd, capture_output=True, timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    raw = res.stdout.decode("utf-8", "replace") or "{}"
    data = json.loads(raw)

    fmt = data.get("format", {}) or {}
    info = MediaInfo(
        path=path,
        duration=_to_float(fmt.get("duration")),
        size=_to_int(fmt.get("size")) or (path.stat().st_size if path.exists() else 0),
        format_name=(fmt.get("format_name") or "").split(",")[0],
        bit_rate=_to_int(fmt.get("bit_rate")),
    )

    for st in data.get("streams", []) or []:
        kind = st.get("codec_type", "")
        s = StreamInfo(
            index=_to_int(st.get("index")),
            codec_type=kind,
            codec_name=st.get("codec_name", "") or "",
            width=_to_int(st.get("width")),
            height=_to_int(st.get("height")),
            fps=_parse_fps(st.get("avg_frame_rate") or st.get("r_frame_rate")),
            bit_rate=_to_int(st.get("bit_rate")),
            channels=_to_int(st.get("channels")),
            channel_layout=st.get("channel_layout", "") or "",
            sample_rate=_to_int(st.get("sample_rate")),
            pix_fmt=st.get("pix_fmt", "") or "",
            language=(st.get("tags", {}) or {}).get("language", "") or "",
            title=(st.get("tags", {}) or {}).get("title", "") or "",
            duration=_to_float(st.get("duration")),
        )
        if kind == "video" and not s.codec_name.startswith("mjpeg"):
            if info.video is None:
                info.video = s
        elif kind == "audio":
            info.audios.append(s)
        elif kind == "subtitle":
            info.subtitles.append(s)

    if not info.duration:
        cands = [s.duration for s in info.audios + info.subtitles if s.duration]
        if info.video and info.video.duration:
            cands.append(info.video.duration)
        if cands:
            info.duration = max(cands)
    return info


_FFMPEG_INFO_KEYS = {
    "Duration": "duration", "bitrate": "bit_rate", "Stream #": "stream",
}

# 布局名 → 声道数（用于 `ffmpeg -i` 文本解析，也是识别 5.1／7.1 的依据）
_LAYOUT_CHANNELS = {
    "mono": 1, "stereo": 2, "2.1": 3, "3.0": 3, "3.0(back)": 3,
    "quad": 4, "quad(side)": 4, "4.0": 4,
    "5.0": 5, "5.0(side)": 5, "5.1": 6, "5.1(side)": 6,
    "6.0": 6, "6.0(front)": 6, "hexagonal": 6,
    "6.1": 7, "6.1(back)": 7, "6.1(front)": 7,
    "7.0": 7, "7.0(front)": 7,
    "7.1": 8, "7.1(wide)": 8, "7.1(wide-side)": 8, "octagonal": 8,
}


def parse_layout(raw: str) -> tuple[str, int]:
    """把 ffmpeg 文本里的声道描述拆成 (布局名, 声道数)。

    布局名认不出来时返回 ("", 声道数)，交给上层按声道数兜底。
    """
    import re as _re

    text = (raw or "").strip().lower()
    if not text:
        return "", 0
    m = _re.match(r"^(\d+)\s*(?:channels?|ch)$", text)
    if m:
        return "", int(m.group(1))
    if text in _LAYOUT_CHANNELS:
        return text, _LAYOUT_CHANNELS[text]
    m = _re.match(r"^([\d.]+)", text)          # 形如 5.1(back) 的变体
    if m:
        prefix = m.group(1)
        if prefix in _LAYOUT_CHANNELS:
            return text, _LAYOUT_CHANNELS[prefix]
    return "", 0


def probe_with_ffmpeg(env: FFmpegEnv, path: Path) -> MediaInfo:
    """没有 ffprobe 时的降级方案：解析 `ffmpeg -i` 的 stderr。"""
    import re

    res = subprocess.run(
        [env.ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
    )
    text = res.stderr.decode("utf-8", "replace")

    info = MediaInfo(
        path=path,
        size=path.stat().st_size if path.exists() else 0,
        format_name=path.suffix.lstrip("."),
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if m:
        info.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
    if m:
        info.bit_rate = int(m.group(1)) * 1000

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Stream #"):
            continue
        sm = re.match(r"Stream #(\d+):(\d+)", line)
        idx = int(sm.group(1)) if sm else 0
        if "Video:" in line:
            v = StreamInfo(index=idx, codec_type="video")
            cm = re.search(r"Video:\s*([a-zA-Z0-9_]+)", line)
            v.codec_name = cm.group(1) if cm else ""
            rm = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if rm:
                v.width, v.height = int(rm.group(1)), int(rm.group(2))
            fm = re.search(r"([\d.]+)\s*fps", line)
            if fm:
                v.fps = _to_float(fm.group(1))
            if info.video is None:
                info.video = v
        elif "Audio:" in line:
            a = StreamInfo(index=idx, codec_type="audio")
            cm = re.search(r"Audio:\s*([a-zA-Z0-9_]+)", line)
            a.codec_name = cm.group(1) if cm else ""
            hm = re.search(r"(\d+)\s*Hz", line)
            if hm:
                a.sample_rate = int(hm.group(1))
            # 采样率之后紧跟的就是声道布局，例如 "48000 Hz, 5.1(side), fltp"
            lm = re.search(r"Hz,\s*([^,]+)", line)
            if lm:
                a.channel_layout, a.channels = parse_layout(lm.group(1))
            if not a.channels:
                if "stereo" in line:
                    a.channels, a.channel_layout = 2, "stereo"
                elif "mono" in line:
                    a.channels, a.channel_layout = 1, "mono"
            info.audios.append(a)
        elif "Subtitle:" in line:
            s = StreamInfo(index=idx, codec_type="subtitle")
            cm = re.search(r"Subtitle:\s*([a-zA-Z0-9_]+)", line)
            s.codec_name = cm.group(1) if cm else ""
            info.subtitles.append(s)
    return info


def probe(env: FFmpegEnv, path: Path) -> MediaInfo:
    if not env.ready:
        return MediaInfo(path=path, error="未找到 ffmpeg")
    if env.has_probe:
        try:
            info = probe_with_ffprobe(env, path)
            if info.ok:
                return info
        except Exception:  # noqa: BLE001
            pass  # ffprobe 不可用或输出异常，转用 ffmpeg 兜底
    try:
        return probe_with_ffmpeg(env, path)
    except Exception as exc:  # noqa: BLE001
        return MediaInfo(path=path, error=f"探测失败：{exc}")


def find_subtitles(path: Path) -> list[Path]:
    """查找与视频同名的外挂字幕。"""
    out: list[Path] = []
    for ext in SUB_EXTS:
        cand = path.with_suffix(ext)
        if cand.exists():
            out.append(cand)
    return out
