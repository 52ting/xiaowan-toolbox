"""音频提取页。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QWidget

from ...core.pipeline import build_audio_task
from ...core.presets import (AUDIO_BITRATES, AUDIO_FORMATS, CHANNEL_OPTIONS,
                             SAMPLE_RATES)
from ...core.utils import AUDIO_EXTS, VIDEO_EXTS, human_size
from ..widgets import Card, FileTable, label
from .shared import ActionBar, OutputRow, Page, make_file_toolbar


class AudioPage(Page):
    requestQueue = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, "音频提取",
                         "从视频里抽出音轨，或把音频转成别的格式。支持批量。")

        self.table = FileTable(ctx.env_provider, VIDEO_EXTS | AUDIO_EXTS)
        card_files = Card()
        card_files.add(make_file_toolbar(self.table, VIDEO_EXTS | AUDIO_EXTS))
        card_files.layout().addWidget(self.table, 1)

        # 底部操作栏要先创建：参数区在构建过程中就会回调 _update_hint
        self.bar = ActionBar("开始提取")
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
        card.add(label("输出参数", "SectionTitle"))

        self.cmb_format = QComboBox()
        for ext, text, _codec, _br in AUDIO_FORMATS:
            self.cmb_format.addItem(text, ext)
        self.cmb_format.currentIndexChanged.connect(self._on_format_changed)

        self.cmb_bitrate = QComboBox()
        for br in AUDIO_BITRATES:
            self.cmb_bitrate.addItem(f"{br} kbps", br)
        self.cmb_bitrate.setCurrentIndex(1)

        self.cmb_rate = QComboBox()
        for value, text in SAMPLE_RATES:
            self.cmb_rate.addItem(text, value)

        self.cmb_channels = QComboBox()
        for value, text in CHANNEL_OPTIONS:
            self.cmb_channels.addItem(text, value)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.addWidget(label("输出格式"), 0, 0)
        grid.addWidget(self.cmb_format, 0, 1)
        grid.addWidget(label("码率"), 0, 2)
        grid.addWidget(self.cmb_bitrate, 0, 3)
        grid.addWidget(label("采样率"), 1, 0)
        grid.addWidget(self.cmb_rate, 1, 1)
        grid.addWidget(label("声道"), 1, 2)
        grid.addWidget(self.cmb_channels, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        card.add(label("无损格式（FLAC / WAV）会忽略码率设置；"
                       "想完全不重编码请选「原样复制音轨」。", "Hint"))
        self._on_format_changed()
        return card

    # ------------------------------------------------------------------ #
    def _format_spec(self):
        ext = self.cmb_format.currentData()
        for e, _text, codec, br in AUDIO_FORMATS:
            if e == ext:
                return e, codec, br
        return "mp3", "libmp3lame", "320"

    def _on_format_changed(self) -> None:
        _ext, codec, _br = self._format_spec()
        lossless = codec in ("flac", "pcm_s16le")
        self.cmb_bitrate.setEnabled(not lossless and codec != "copy")
        self._update_hint()

    def _update_hint(self) -> None:
        paths = self.table.paths
        if not paths:
            self.bar.set_hint("还没有添加文件 —— 拖入视频或音频文件即可")
            self.bar.button.setEnabled(False)
            return
        if not self.ctx.env.ready:
            self.bar.set_hint("未找到 ffmpeg，请到「设置」里指定路径", "Danger")
            self.bar.button.setEnabled(False)
            return
        self.bar.button.setEnabled(True)
        info = self.table.info_for(paths[0])
        prefix = f"共 {len(paths)} 个任务 · " if len(paths) > 1 else ""
        if info and info.audios:
            est = 0.0
            codec = self._format_spec()[1]
            if codec == "copy" and info.audios[0].bit_rate:
                est = info.audios[0].bit_rate * info.duration / 8
            elif codec in ("flac", "pcm_s16le"):
                a = info.audios[0]
                raw = (a.sample_rate or 44100) * (a.channels or 2) * 2
                est = raw * info.duration * (0.6 if codec == "flac" else 1.0)
            else:
                est = int(self.cmb_bitrate.currentData() or 192) * 1000 * info.duration / 8
            self.bar.set_hint(f"{prefix}首个文件约 {human_size(est)}"
                              f"（时长 {int(info.duration // 60)} 分 {int(info.duration % 60)} 秒）")
        elif info and info.error:
            self.bar.set_hint(f"{prefix}{info.error}", "Danger")
        else:
            self.bar.set_hint(f"{prefix}读取文件信息中…")

    # ------------------------------------------------------------------ #
    def _on_start(self) -> None:
        paths = self.table.paths
        if not paths or not self.ctx.env.ready:
            return
        ext, codec, default_br = self._format_spec()
        bitrate = 0 if codec in ("flac", "pcm_s16le", "copy") else int(
            self.cmb_bitrate.currentData() or default_br or 192)
        rate = int(self.cmb_rate.currentData() or 0)
        channels = int(self.cmb_channels.currentData() or 0)

        tasks = []
        for path in paths:
            info = self.table.info_for(path)
            tasks.append(build_audio_task(
                self.ctx, path, info, ext, codec, bitrate, rate, channels))

        self.ctx.queue.add_many(tasks)
        self.table.clear_all()
        self.requestQueue.emit()
