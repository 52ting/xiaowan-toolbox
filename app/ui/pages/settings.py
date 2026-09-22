"""设置页：ffmpeg 路径、输出偏好、并发与外观。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QSpinBox,
                               QWidget)

from ...core.utils import (default_output_dir, ffmpeg_install_hint,
                           is_macos, is_windows, open_in_file_manager)
from ..widgets import Card, Pill, label
from .shared import Page


class SettingsPage(Page):
    themeChanged = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, "设置", "一般保持默认就能用。找不到 ffmpeg 时再来这里指定。")
        self.root.addWidget(self._build_env())
        self.root.addWidget(self._build_output())
        self.root.addWidget(self._build_prefs())

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addStretch(1)
        self.btn_reset = QPushButton("恢复默认")
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setObjectName("Primary")
        bl.addWidget(self.btn_reset)
        bl.addWidget(self.btn_save)
        self.root.addWidget(bar)
        self.root.addStretch(1)

        self.btn_save.clicked.connect(self._save)
        self.btn_reset.clicked.connect(self._reset)
        self._load()

    # ------------------------------------------------------------------ #
    def _build_env(self) -> QWidget:
        card = Card()
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(label("运行环境", "SectionTitle"))
        hl.addStretch(1)
        self.env_pill = Pill("未检测", "err")
        hl.addWidget(self.env_pill)
        card.add(head)

        self.ed_ffmpeg = QLineEdit()
        self.ed_ffmpeg.setPlaceholderText("留空 = 自动检测（PATH / 应用内置 / 系统常见位置）")
        btn_ff = QPushButton("浏览…")
        btn_ff.clicked.connect(lambda: self._pick_file(self.ed_ffmpeg, "选择 ffmpeg"))
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(8)
        r1.addWidget(label("ffmpeg 路径"))
        r1.addWidget(self.ed_ffmpeg, 1)
        r1.addWidget(btn_ff)
        card.add(row1)

        self.ed_ffprobe = QLineEdit()
        self.ed_ffprobe.setPlaceholderText("留空 = 自动检测（一般无需填写）")
        btn_fp = QPushButton("浏览…")
        btn_fp.clicked.connect(lambda: self._pick_file(self.ed_ffprobe, "选择 ffprobe"))
        row2 = QWidget()
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(8)
        r2.addWidget(label("ffprobe 路径"))
        r2.addWidget(self.ed_ffprobe, 1)
        r2.addWidget(btn_fp)
        card.add(row2)

        act = QWidget()
        al = QHBoxLayout(act)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        self.btn_detect = QPushButton("重新检测")
        self.btn_ver = QPushButton("查看编码器支持")
        al.addWidget(self.btn_detect)
        al.addWidget(self.btn_ver)
        al.addStretch(1)
        card.add(act)

        self.btn_detect.clicked.connect(self._detect)
        self.btn_ver.clicked.connect(self._show_encoders)

        self.env_detail = label("", "Hint", wrap=True)
        card.add(self.env_detail)

        card.add(label(f"没装 ffmpeg？{ffmpeg_install_hint()}。", "Hint"))
        return card

    def _build_output(self) -> QWidget:
        card = Card()
        card.add(label("输出", "SectionTitle"))

        self.ed_outdir = QLineEdit()
        btn_dir = QPushButton("选择…")
        btn_dir.clicked.connect(self._pick_dir)
        btn_open = QPushButton("打开")
        btn_open.clicked.connect(
            lambda: open_in_file_manager(Path(self.ed_outdir.text()))
            if Path(self.ed_outdir.text()).exists() else None)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        rl.addWidget(label("默认输出目录"))
        rl.addWidget(self.ed_outdir, 1)
        rl.addWidget(btn_dir)
        rl.addWidget(btn_open)
        card.add(row)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("与源文件相同目录", "source")
        self.cmb_mode.addItem("始终输出到上面的目录", "custom")

        self.ed_suffix = QLineEdit()
        self.ed_suffix.setPlaceholderText("例如 _压缩（留空则仅在重名时自动加序号）")
        self.ed_suffix.setMaximumWidth(240)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.addWidget(label("默认位置"), 0, 0)
        grid.addWidget(self.cmb_mode, 0, 1)
        grid.addWidget(label("文件名后缀"), 1, 0)
        grid.addWidget(self.ed_suffix, 1, 1)
        grid.setColumnStretch(1, 1)
        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)

        self.chk_overwrite = QCheckBox("直接覆盖同名文件（默认关闭，会自动加 _1 序号）")
        self.chk_after = QCheckBox("队列跑完后打开输出目录")
        card.add(self.chk_overwrite)
        card.add(self.chk_after)
        return card

    def _build_prefs(self) -> QWidget:
        card = Card()
        card.add(label("性能与外观", "SectionTitle"))

        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 8)
        self.spin_workers.setSuffix(" 个")

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("浅色", "light")
        self.cmb_theme.addItem("深色", "dark")

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.addWidget(label("同时处理"), 0, 0)
        grid.addWidget(self.spin_workers, 0, 1)
        grid.addWidget(label("主题"), 0, 2)
        grid.addWidget(self.cmb_theme, 0, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        card.add(label("x264 / x265 已自带多线程，一般设 1–2 就够；"
                       "设太高反而会互相抢 CPU，总体更慢。", "Hint"))
        return card

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        cfg = self.ctx.config
        self.ed_ffmpeg.setText(cfg.ffmpeg_path)
        self.ed_ffprobe.setText(cfg.ffprobe_path)
        self.ed_outdir.setText(cfg.output_dir or str(default_output_dir()))
        idx = self.cmb_mode.findData(cfg.output_mode)
        self.cmb_mode.setCurrentIndex(max(0, idx))
        self.ed_suffix.setText(cfg.filename_suffix)
        self.chk_overwrite.setChecked(cfg.overwrite)
        self.chk_after.setChecked(cfg.open_when_done)
        self.spin_workers.setValue(max(1, min(8, cfg.concurrency)))
        tidx = self.cmb_theme.findData(cfg.theme)
        self.cmb_theme.setCurrentIndex(max(0, tidx))
        self._detect(apply=False)

    def _pick_file(self, line_edit: QLineEdit, title: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, str(Path.home()))
        if path:
            line_edit.setText(path)

    def _pick_dir(self) -> None:
        start = self.ed_outdir.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择默认输出目录", start)
        if path:
            self.ed_outdir.setText(path)

    def _detect(self, apply: bool = True) -> None:
        ffmpeg = self.ed_ffmpeg.text().strip()
        ffprobe = self.ed_ffprobe.text().strip()
        if apply:
            self.ctx.config.ffmpeg_path = ffmpeg
            self.ctx.config.ffprobe_path = ffprobe
        env = self.ctx.refresh_env()

        if env.ready:
            self.env_pill.setText("就绪")
            self.env_pill.set_kind("ok")
            probe_note = "ffprobe 已找到（读取信息更快）" if env.has_probe \
                else "未找到 ffprobe，将用 ffmpeg 兜底解析"
            self.env_detail.setText(
                f"{env.version or 'ffmpeg 已找到'}\n路径：{env.ffmpeg}\n{probe_note}")
        else:
            self.env_pill.setText("未找到")
            self.env_pill.set_kind("err")
            self.env_detail.setText(
                f"没有检测到 ffmpeg。{ffmpeg_install_hint()}，"
                "或手动指定可执行文件路径。")

    def _show_encoders(self) -> None:
        env = self.ctx.env
        if not env.ready:
            self.env_detail.setText("请先配置好 ffmpeg。")
            return
        wanted = [("libx264", "H.264 软编"), ("libx265", "H.265 软编"),
                  ("h264_videotoolbox", "H.264 硬编"), ("hevc_videotoolbox", "H.265 硬编"),
                  ("libmp3lame", "MP3"), ("aac", "AAC"), ("libopus", "Opus"),
                  ("flac", "FLAC"), ("libvpx-vp9", "VP9")]
        lines = []
        for name, label_cn in wanted:
            ok = env.encoder_available(name)
            lines.append(f"{'✔' if ok else '✘'} {label_cn}（{name}）")
        self.env_detail.setText(" ".join(lines))

    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        cfg = self.ctx.config
        cfg.ffmpeg_path = self.ed_ffmpeg.text().strip()
        cfg.ffprobe_path = self.ed_ffprobe.text().strip()
        cfg.output_dir = self.ed_outdir.text().strip()
        cfg.output_mode = self.cmb_mode.currentData() or "source"
        cfg.filename_suffix = self.ed_suffix.text().strip()
        cfg.overwrite = self.chk_overwrite.isChecked()
        cfg.open_when_done = self.chk_after.isChecked()
        cfg.concurrency = self.spin_workers.value()
        cfg.theme = self.cmb_theme.currentData() or "light"

        self.ctx.save_config()
        self.ctx.refresh_env()
        self.themeChanged.emit(cfg.theme)
        self._detect(apply=False)
        self.env_detail.setText(self.env_detail.text() + "\n已保存。")

    def _reset(self) -> None:
        cfg = self.ctx.config
        cfg.ffmpeg_path = ""
        cfg.ffprobe_path = ""
        cfg.output_mode = "source"
        cfg.output_dir = str(default_output_dir())
        cfg.filename_suffix = ""
        cfg.overwrite = False
        cfg.open_when_done = False
        cfg.concurrency = 2
        cfg.theme = "light"
        self.ctx.save_config()
        self._load()
        self.themeChanged.emit(cfg.theme)
        self.env_detail.setText(self.env_detail.text() + "\n已恢复默认。")
