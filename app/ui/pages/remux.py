"""封装 / 混流页（换容器、挑音轨、嵌字幕）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QSpinBox, QWidget)

from ...core.pipeline import build_remux_task
from ...core.presets import REMUX_CONTAINERS, Container
from ...core.utils import VIDEO_EXTS, human_size
from ..widgets import Card, FileTable, label
from .shared import ActionBar, OutputRow, Page, make_file_toolbar

AUDIO_MODES = [
    ("all", "保留全部音轨"),
    ("first", "只保留第一条"),
    ("none", "不要音轨"),
]

AUDIO_CODECS = [
    ("copy", "原样复制（不重编码）"),
    ("aac", "转成 AAC"),
    ("libmp3lame", "转成 MP3"),
]


class RemuxPage(Page):
    requestQueue = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, "封装混流",
                         "换容器、挑音轨、挂字幕。默认不重新编码，速度极快、画质无损。")

        self.table = FileTable(ctx.env_provider, VIDEO_EXTS)
        card_files = Card()
        card_files.add(make_file_toolbar(self.table, VIDEO_EXTS))
        card_files.layout().addWidget(self.table, 1)

        # 底部操作栏要先创建：参数区在构建过程中就会回调 _update_hint
        self.bar = ActionBar("开始封装")
        self.bar.button.clicked.connect(self._on_start)

        self.root.addWidget(card_files, 1)
        self.root.addWidget(self._build_params())
        self.root.addWidget(OutputRow(ctx))
        self.root.addWidget(self.bar)

        self.table.filesChanged.connect(self._update_hint)
        self._update_hint()

    # ------------------------------------------------------------------ #
    def _build_params(self) -> QWidget:
        card = Card()
        card.add(label("封装选项", "SectionTitle"))

        self.cmb_container = QComboBox()
        for container, text, desc in REMUX_CONTAINERS:
            self.cmb_container.addItem(f"{text} —— {desc}", container.value)
        self.cmb_container.setCurrentIndex(1)  # 默认 MKV，兼容性最稳
        self.cmb_container.currentIndexChanged.connect(self._on_container_changed)

        self.cmb_audio_mode = QComboBox()
        for value, text in AUDIO_MODES:
            self.cmb_audio_mode.addItem(text, value)
        self.cmb_audio_mode.currentIndexChanged.connect(self._on_audio_mode_changed)

        self.cmb_audio_codec = QComboBox()
        for value, text in AUDIO_CODECS:
            self.cmb_audio_codec.addItem(text, value)
        self.cmb_audio_codec.currentIndexChanged.connect(self._on_audio_mode_changed)

        self.spin_audio_br = QSpinBox()
        self.spin_audio_br.setRange(32, 512)
        self.spin_audio_br.setValue(192)
        self.spin_audio_br.setSuffix(" kbps")

        audio_row = QWidget()
        al = QHBoxLayout(audio_row)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        al.addWidget(self.cmb_audio_codec, 1)
        al.addWidget(self.spin_audio_br)

        self.chk_subtitles = QCheckBox("嵌入同名字幕文件（.srt / .ass / .vtt）")
        self.chk_subtitles.setChecked(True)
        self.chk_faststart = QCheckBox("MP4 启用 faststart（网页秒开）")
        self.chk_faststart.setChecked(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.addWidget(label("目标容器"), 0, 0)
        grid.addWidget(self.cmb_container, 0, 1)
        grid.addWidget(label("音轨"), 0, 2)
        grid.addWidget(self.cmb_audio_mode, 0, 3)
        grid.addWidget(label("音轨编码"), 1, 0)
        grid.addWidget(audio_row, 1, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        card.add(self.chk_subtitles)
        card.add(self.chk_faststart)
        self.note = label("", "Hint")
        card.add(self.note)
        self._on_container_changed()
        return card

    # ------------------------------------------------------------------ #
    def current_container(self) -> Container:
        return Container(self.cmb_container.currentData() or "mkv")

    def _on_container_changed(self) -> None:
        container = self.current_container()
        self.chk_faststart.setEnabled(container == Container.MP4)
        if container == Container.WEBM:
            self.note.setText("WebM 只支持 VP9 / Opus，无法直接复制 H.264 流，"
                              "遇到失败请改用 MKV。")
        elif container == Container.MP4:
            self.note.setText("MP4 对音轨格式挑剔，若源音轨是 AC3 / DTS / Opus，"
                              "建议把「音轨编码」改成转 AAC。")
        else:
            self.note.setText("MKV 什么都能装：多音轨、多字幕、章节都会保留。")
        self._update_hint()

    def _on_audio_mode_changed(self) -> None:
        mode = self.cmb_audio_mode.currentData()
        codec = self.cmb_audio_codec.currentData()
        self.cmb_audio_codec.setEnabled(mode != "none")
        self.spin_audio_br.setEnabled(mode != "none" and codec != "copy")
        self._update_hint()

    def _update_hint(self) -> None:
        paths = self.table.paths
        if not paths:
            self.bar.set_hint("还没有添加文件 —— 拖入视频即可")
            self.bar.button.setEnabled(False)
            return
        if not self.ctx.env.ready:
            self.bar.set_hint("未找到 ffmpeg，请到「设置」里指定路径", "Danger")
            self.bar.button.setEnabled(False)
            return
        self.bar.button.setEnabled(True)

        info = self.table.info_for(paths[0])
        prefix = f"共 {len(paths)} 个任务 · " if len(paths) > 1 else ""
        if not info:
            self.bar.set_hint(f"{prefix}读取文件信息中…")
            return
        tracks = []
        if info.audios:
            tracks.append(f"{len(info.audios)} 条音轨")
        if info.subtitles:
            tracks.append(f"{len(info.subtitles)} 条内嵌字幕")
        detail = "、".join(tracks) if tracks else "只有视频轨"
        action = "不重新编码，秒级完成" if self.cmb_audio_codec.currentData() == "copy" \
            else "音频需重新编码"
        self.bar.set_hint(
            f"{prefix}{detail} · 原文件 {human_size(info.size)} · {action}")

    # ------------------------------------------------------------------ #
    def _on_start(self) -> None:
        paths = self.table.paths
        if not paths or not self.ctx.env.ready:
            return
        container = self.current_container()
        audio_mode = self.cmb_audio_mode.currentData() or "all"
        audio_codec = self.cmb_audio_codec.currentData() or "copy"
        bitrate = self.spin_audio_br.value()

        tasks = []
        for path in paths:
            info = self.table.info_for(path)
            tasks.append(build_remux_task(
                self.ctx, path, info, container,
                audio_mode=audio_mode, audio_codec=audio_codec,
                audio_bitrate_kbps=bitrate,
                embed_subtitles=self.chk_subtitles.isChecked(),
                faststart=self.chk_faststart.isChecked(),
            ))

        self.ctx.queue.add_many(tasks)
        self.table.clear_all()
        self.requestQueue.emit()
