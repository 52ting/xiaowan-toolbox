"""应用配置的读写（存 JSON，Mac 上落在 ~/Library/Application Support）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .utils import config_path, default_output_dir


@dataclass
class AppConfig:
    # 二进制
    ffmpeg_path: str = ""          # 留空 = 自动检测
    ffprobe_path: str = ""

    # 输出
    output_mode: str = "source"    # source = 与源文件同目录 / custom = 指定目录
    output_dir: str = ""
    filename_suffix: str = ""      # 输出文件名后缀，如 "_压缩"
    overwrite: bool = False

    # 队列
    concurrency: int = 2
    open_when_done: bool = False
    remove_source: bool = False    # 成功后删除源文件（默认关闭）

    # 界面
    theme: str = "light"
    last_preset: str = "balanced"
    last_downmix_preset: str = "standard"
    downmix_merge: bool = False           # 下混页是否处于「多轨合并」模式
    downmix_merge_layout: str = "auto"    # 合并时的声道布局
    window_geometry: str = ""

    # 高级
    hw_checked: bool = False
    available_encoders: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.output_dir = str(default_output_dir())
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = cls()
            cfg.output_dir = str(default_output_dir())
            return cfg
        valid = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in valid}
        cfg = cls(**kwargs)
        if not cfg.output_dir:
            cfg.output_dir = str(default_output_dir())
        return cfg

    def save(self) -> None:
        try:
            config_path().write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def resolved_output_dir(self, source: Path) -> Path:
        if self.output_mode == "custom" and self.output_dir:
            out = Path(self.output_dir)
        else:
            out = source.parent
        out.mkdir(parents=True, exist_ok=True)
        return out
