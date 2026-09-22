"""应用上下文：配置、ffmpeg 环境、任务队列，全应用共享。"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .core.config import AppConfig
from .core.engine import QueueManager
from .core.ffmpeg import FFmpegEnv


class AppContext(QObject):
    envChanged = Signal()
    configChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config: AppConfig = AppConfig.load()
        self.env: FFmpegEnv = FFmpegEnv().refresh(
            self.config.ffmpeg_path, self.config.ffprobe_path)
        self.queue = QueueManager(self.env_provider, self.config_provider)

    # ---------------- provider ---------------- #
    def env_provider(self) -> FFmpegEnv:
        return self.env

    def config_provider(self) -> AppConfig:
        return self.config

    # ---------------- 变更 ---------------- #
    def refresh_env(self) -> FFmpegEnv:
        self.env = FFmpegEnv().refresh(
            self.config.ffmpeg_path, self.config.ffprobe_path)
        self.envChanged.emit()
        return self.env

    def save_config(self) -> None:
        self.config.save()
        self.configChanged.emit()

    def apply_config(self, cfg: AppConfig) -> None:
        self.config = cfg
        self.save_config()
        self.refresh_env()
