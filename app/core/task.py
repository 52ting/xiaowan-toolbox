"""任务模型。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .commands import CommandStep
from .ffmpeg import MediaInfo


class TaskKind(str, Enum):
    COMPRESS = "compress"
    AUDIO = "audio"
    REMUX = "remux"
    DOWNMIX = "downmix"


KIND_LABEL = {
    TaskKind.COMPRESS: "视频压缩",
    TaskKind.AUDIO: "音频提取",
    TaskKind.REMUX: "封装混流",
    TaskKind.DOWNMIX: "声道下混",
}


class TaskStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


STATUS_LABEL = {
    TaskStatus.WAITING: "等待中",
    TaskStatus.RUNNING: "进行中",
    TaskStatus.DONE: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELED: "已取消",
}


@dataclass
class Task:
    kind: TaskKind
    src: Path
    dst: Path
    steps: list[CommandStep] = field(default_factory=list)
    info: MediaInfo | None = None
    work_dir: Path | None = None
    # 多文件合并出来的任务：src 只是代表项，真正的输入都在 sources 里
    sources: list[Path] = field(default_factory=list)
    label: str = ""                  # 覆盖默认显示名（合并任务用）

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.WAITING
    progress: float = 0.0            # 0.0 ~ 1.0
    speed: str = ""
    eta: float = -1.0                # 剩余秒数，-1 未知
    message: str = ""
    error: str = ""

    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0

    out_size: int = 0
    log: list[str] = field(default_factory=list)
    process: object | None = None    # subprocess.Popen
    cancel_requested: bool = False

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self.label or self.src.name

    @property
    def source_tooltip(self) -> str:
        paths = self.sources or [self.src]
        if len(paths) == 1:
            return str(paths[0])
        return f"{len(paths)} 个文件合成：\n" + "\n".join(str(p) for p in paths)

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, "任务")

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, "未知")

    @property
    def duration(self) -> float:
        return self.info.duration if self.info else 0.0

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        end = self.finished or time.time()
        return max(0.0, end - self.started)

    @property
    def ratio(self) -> float:
        """压缩比：输出 / 输入。"""
        if self.out_size and self.src.exists():
            try:
                return self.out_size / max(self.src.stat().st_size, 1)
            except OSError:
                return 0.0
        return 0.0

    @property
    def is_finished(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELED)

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.RUNNING, TaskStatus.WAITING)

    def append_log(self, line: str, limit: int = 500) -> None:
        self.log.append(line)
        if len(self.log) > limit:
            del self.log[: len(self.log) - limit]

    def tail(self, n: int = 40) -> str:
        return "\n".join(self.log[-n:])
