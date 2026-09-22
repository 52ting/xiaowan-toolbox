"""通用工具：体积/时长格式化、路径处理、平台目录。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "XiaoWanToolbox"
APP_TITLE = "小丸工具箱"

VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".rmvb", ".3gp", ".ogv",
}
AUDIO_EXTS = {
    ".mp3", ".aac", ".m4a", ".flac", ".wav", ".ape", ".ogg", ".opus",
    ".wma", ".alac", ".aiff", ".ac3", ".dts", ".amr",
}
SUB_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def platform_label() -> str:
    """界面文案里的平台名：'Mac' / 'Windows' / 'Linux'。"""
    if is_macos():
        return "Mac"
    if is_windows():
        return "Windows"
    return "Linux"


def file_manager_label() -> str:
    """系统文件管理器的中文名。"""
    if is_macos():
        return "访达"
    if is_windows():
        return "资源管理器"
    return "文件管理器"


def ffmpeg_install_hint() -> str:
    """按平台给出安装 / 指定 ffmpeg 的提示。"""
    if is_macos():
        return "brew install ffmpeg"
    if is_windows():
        return "到 gyan.dev 或 BtbN 的 GitHub 发布页下载 ffmpeg，再把 bin\\ffmpeg.exe 填进来"
    return "用包管理器安装，例如 sudo apt install ffmpeg"


def human_size(num: float | int | None) -> str:
    """1536 -> '1.50 KB'"""
    if num is None:
        return "—"
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "—"
    if num < 0:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(num)} B"
    return f"{num:.2f} {units[idx]}"


def human_duration(seconds: float | None) -> str:
    """3725 -> '01:02:05'"""
    if seconds is None or seconds < 0:
        return "—"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def human_bitrate(bps: float | None) -> str:
    if not bps or bps <= 0:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    return f"{bps / 1000:.0f} kbps"


def parse_bitrate(text: str | int | None) -> int:
    """'128k' / '2M' / 128000 -> bps"""
    if text is None:
        return 0
    if isinstance(text, int):
        return text
    t = str(text).strip().lower()
    try:
        if t.endswith("k"):
            return int(float(t[:-1]) * 1000)
        if t.endswith("m"):
            return int(float(t[:-1]) * 1_000_000)
        return int(float(t))
    except ValueError:
        return 0


def data_dir() -> Path:
    """平台标准配置目录。"""
    if is_macos():
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif is_windows():
        base = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


def default_output_dir() -> Path:
    """默认输出目录：~/Movies/小丸输出（Mac）/ ~/Videos/小丸输出（Win）"""
    if is_macos():
        base = Path.home() / "Movies"
    elif is_windows():
        base = Path.home() / "Videos"
    else:
        base = Path.home() / "Videos"
    out = base / "小丸输出"
    out.mkdir(parents=True, exist_ok=True)
    return out


def unique_path(path: Path) -> Path:
    """若目标已存在，追加 _1 / _2 … 避免覆盖。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    idx = 1
    while True:
        cand = parent / f"{stem}_{idx}{suffix}"
        if not cand.exists():
            return cand
        idx += 1


def open_in_file_manager(path: Path) -> None:
    """在访达 / 资源管理器中显示文件或目录。"""
    p = str(path)
    try:
        if is_macos():
            if Path(p).is_file():
                subprocess.Popen(["open", "-R", p])
            else:
                subprocess.Popen(["open", p])
        elif is_windows():
            if Path(p).is_file():
                subprocess.Popen(["explorer", "/select,", p])
            else:
                os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(Path(p).parent if Path(p).is_file() else p)])
    except Exception:
        pass


def collect_media_files(paths: list[str], exts: set[str], recursive: bool = False) -> list[Path]:
    """展开目录、按扩展名过滤、去重并保持顺序。"""
    result: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        key = str(p.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        result.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for child in sorted(it):
                if child.is_file() and child.suffix.lower() in exts:
                    _add(child)
        elif p.is_file():
            _add(p)
    return result


def safe_stem(path: Path) -> str:
    """去掉文件名里对 ffmpeg 不友好的字符。"""
    bad = '<>:"/\\|?*\n\r\t'
    name = "".join("_" if c in bad else c for c in path.stem).strip()
    return name or "output"
