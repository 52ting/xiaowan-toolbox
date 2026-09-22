"""主窗口：左侧导航 + 右侧页面栈。"""

from __future__ import annotations

import base64
import re

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..context import AppContext
from ..core.task import TaskStatus
from ..core.utils import ffmpeg_install_hint, platform_label
from .pages.audio import AudioPage
from .pages.compress import CompressPage
from .pages.downmix import DownmixPage
from .pages.queue import QueuePage
from .pages.remux import RemuxPage
from .pages.settings import SettingsPage
from .style import palette
from .widgets import FileTable, label

NAV_ITEMS = [
    ("视频压缩", "把大视频压小，日常主力功能"),
    ("音频提取", "从视频里取音频，或转换音频格式"),
    ("声道下混", "多声道转立体声；多个单声道文件合成一条"),
    ("封装混流", "换容器 / 挑音轨 / 嵌字幕，不重编码"),
    ("任务队列", "查看进度、速度与日志"),
    ("设置", "ffmpeg 路径、输出偏好、外观"),
]

# 任务队列页在导航里的位置（按名字查，避免以后插页面时索引错位）
QUEUE_INDEX = next(i for i, (name, _) in enumerate(NAV_ITEMS) if name == "任务队列")


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("小丸工具箱")
        self.setMinimumSize(980, 660)
        self.resize(1120, 780)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.compress_page = CompressPage(ctx)
        self.audio_page = AudioPage(ctx)
        self.downmix_page = DownmixPage(ctx)
        self.remux_page = RemuxPage(ctx)
        self.queue_page = QueuePage(ctx)
        self.settings_page = SettingsPage(ctx)
        for page in (self.compress_page, self.audio_page, self.downmix_page,
                     self.remux_page, self.queue_page, self.settings_page):
            self.stack.addWidget(self._wrap_scroll(page))
        layout.addWidget(self.stack, 1)

        for page in (self.compress_page, self.audio_page, self.downmix_page,
                     self.remux_page):
            page.requestQueue.connect(lambda: self.go_to(QUEUE_INDEX))

        self.settings_page.themeChanged.connect(self._apply_theme)
        self.ctx.envChanged.connect(self._refresh_env_badge)

        self._restore_geometry()
        self._refresh_env_badge()
        self.go_to(0)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _wrap_scroll(page: QWidget) -> QScrollArea:
        """页面套滚动容器：窗口不够高时页面可竖向滚动，内容不再被压扁。"""
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setViewportMargins(0, 0, 0, 0)
        sc.viewport().setAutoFillBackground(False)
        sc.setWidget(page)
        return sc

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(216)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)

        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(2, 0, 0, 0)
        bl.setSpacing(10)
        badge = QLabel("丸")
        badge.setObjectName("BrandBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(badge)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(label("小丸工具箱", "BrandTitle"))
        text.addWidget(label(f"{platform_label()} 版 · v{__version__}", "BrandSub"))
        bl.addLayout(text)
        bl.addStretch(1)
        lay.addWidget(brand)
        lay.addSpacing(16)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        for i, (name, tip) in enumerate(NAV_ITEMS):
            btn = QPushButton(f"  {name}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self.go_to(idx))
            self.nav_group.addButton(btn, i)
            self._nav_buttons.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)

        env_box = QWidget()
        el = QVBoxLayout(env_box)
        el.setContentsMargins(4, 0, 4, 0)
        el.setSpacing(2)
        self.env_dot = label("● 检测中", "EnvDot")
        self.env_text = label("", "EnvText", wrap=True)
        el.addWidget(self.env_dot)
        el.addWidget(self.env_text)
        lay.addWidget(env_box)
        return side

    # ------------------------------------------------------------------ #
    def go_to(self, index: int) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        self.stack.setCurrentIndex(index)
        if 0 <= index < len(self._nav_buttons):
            self._nav_buttons[index].setChecked(True)

    def _apply_theme(self, theme: str) -> None:
        from .style import apply_theme

        apply_theme(QApplication.instance(), theme)
        self.ctx.config.theme = theme
        # 让已存在的表格按新主题重新着色
        for table in self.findChildren(FileTable):
            table.refresh_colors()
        self._refresh_env_badge()

    def _refresh_env_badge(self) -> None:
        env = self.ctx.env
        pal = palette(self.ctx.config.theme)
        if env.ready:
            self.env_dot.setText("● ffmpeg 就绪")
            self.env_dot.setStyleSheet(f"color: {pal['success']};")
            detail = "ffprobe 已启用" if env.has_probe else "ffprobe 缺失（功能不受影响）"
            m = re.search(r"ffmpeg version (\S+)", env.version or "")
            ver = (m.group(1) if m else "").split("-")[0][:24]
            self.env_text.setText(f"{detail}\nffmpeg {ver}" if ver else detail)
        else:
            self.env_dot.setText("● 未找到 ffmpeg")
            self.env_dot.setStyleSheet(f"color: {pal['danger']};")
            self.env_text.setText(f"请到「设置」指定路径\n或：{ffmpeg_install_hint()}")

    # ------------------------------------------------------------------ #
    def _restore_geometry(self) -> None:
        data = self.ctx.config.window_geometry
        if data:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(data)))
                return
            except Exception:
                pass
        self._center()

    def _center(self) -> None:
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        running = [t for t in self.ctx.queue.tasks
                   if t.status == TaskStatus.RUNNING]
        if running:
            box = QMessageBox(self)
            box.setWindowTitle("还有任务在跑")
            box.setText(f"还有 {len(running)} 个任务正在处理，确定要退出吗？")
            box.setInformativeText("退出会中断这些任务，已生成的不完整文件会被删除。")
            box.setStandardButtons(QMessageBox.StandardButton.Cancel
                                   | QMessageBox.StandardButton.Yes)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            box.button(QMessageBox.StandardButton.Yes).setText("仍然退出")
            box.button(QMessageBox.StandardButton.Cancel).setText("继续等待")
            if box.exec() != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.ctx.queue.clear_all()

        try:
            geo = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.ctx.config.window_geometry = geo
            self.ctx.save_config()
        except Exception:
            pass
        event.accept()
