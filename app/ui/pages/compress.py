"""视频压缩页。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFrame,
                               QGridLayout, QHBoxLayout, QScrollArea, QSlider,
                               QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ...core.commands import estimate_output_size_mb
from ...core.pipeline import build_compress_task
from ...core.presets import (AUDIO_CODEC_OPTIONS, CONTAINER_LABEL, FPS_OPTIONS,
                             PRESETS, PRESETS_BY_KEY, SCALE_OPTIONS, TUNE_OPTIONS,
                             X264_PRESETS, Container, Preset, RateMode,
                             VideoSettings, codec_labels, is_hw_encoder,
                             resolve_codec)
from ...core.utils import VIDEO_EXTS, human_size
from ..widgets import Card, label
from .shared import ActionBar, OutputRow, Page, PresetChip, make_file_toolbar

MODE_ITEMS = [
    (RateMode.CRF, "恒定质量 CRF（推荐）"),
    (RateMode.BITRATE, "指定视频码率"),
    (RateMode.TARGET_SIZE, "指定输出体积"),
]


class CompressPage(Page):
    requestQueue = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, "视频压缩",
                         "拖入视频，点一个预设就能开始。想自己调参数，往下展开「编码参数」。")
        self._loading = False

        # ---------------- 文件 ---------------- #
        from ..widgets import FileTable
        self.table = FileTable(ctx.env_provider, VIDEO_EXTS)
        card_files = Card()
        card_files.add(make_file_toolbar(self.table, VIDEO_EXTS))
        card_files.layout().addWidget(self.table, 1)
        self.root.addWidget(card_files, 1)

        # ---------------- 预设 ---------------- #
        self.root.addWidget(self._build_presets())

        # ---------------- 参数 ---------------- #
        self.root.addWidget(self._build_params())

        # ---------------- 输出 ---------------- #
        out_row = OutputRow(ctx)
        self.root.addWidget(out_row)

        self.bar = ActionBar("开始压缩")
        self.bar.button.clicked.connect(self._on_start)
        self.root.addWidget(self.bar)

        self.table.filesChanged.connect(self._update_estimate)
        self._select_preset("balanced")
        self._update_estimate()

    # ------------------------------------------------------------------ #
    # 预设区
    # ------------------------------------------------------------------ #
    def _build_presets(self) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        lay.addWidget(label("一键预设", "SectionTitle"))

        from .shared import PresetFlowRow

        flow = PresetFlowRow()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(PresetFlowRow.CHIP_H + 2)
        flow.heightChanged.connect(
            lambda h: scroll.setFixedHeight(h + 2))

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._chips: dict[str, PresetChip] = {}

        for preset in PRESETS:
            chip = PresetChip(preset)
            chip.clicked.connect(lambda _=False, key=preset.key: self._select_preset(key))
            self._group.addButton(chip)
            self._chips[preset.key] = chip
            flow.addChip(chip)

        scroll.setWidget(flow)
        lay.addWidget(scroll)
        return holder

    def _select_preset(self, key: str) -> None:
        preset: Preset | None = PRESETS_BY_KEY.get(key)
        chip = self._chips.get(key)
        if chip:
            chip.setChecked(True)
        if not preset:
            return

        s = preset.video
        panel = self._panel
        panel.show()
        if s.codec == "copy":
            self._set_combo(self.cmb_codec, "copy")
            self._set_combo(self.cmb_container, s.container.value)
            self._mode_index(0)
            self._update_estimate()
            return

        idx = self.cmb_codec.findData(s.codec)
        if idx >= 0:
            self.cmb_codec.setCurrentIndex(idx)
        self._mode_index({
            RateMode.CRF: 0, RateMode.BITRATE: 1, RateMode.TARGET_SIZE: 2,
        }.get(s.rate_mode, 0))
        self.slider_crf.setValue(int(s.crf * 10))
        self.spin_crf.setValue(s.crf)
        self.spin_bitrate.setValue(s.bitrate_kbps)
        self._set_combo(self.cmb_speed, s.preset)
        self._set_combo(self.cmb_tune, s.tune)
        self._set_combo(self.cmb_scale, s.scale_height)
        self._set_combo(self.cmb_fps, s.fps)
        self._set_combo(self.cmb_audio, s.audio_codec)
        self.spin_audio_br.setValue(s.audio_bitrate_kbps)
        self._set_combo(self.cmb_container, s.container.value)
        self.ctx.config.last_preset = key
        self.ctx.save_config()
        self._update_estimate()

    # ------------------------------------------------------------------ #
    # 参数区
    # ------------------------------------------------------------------ #
    def _build_params(self) -> QWidget:
        card = Card()
        card.add(label("编码参数", "SectionTitle"))

        # 编码器
        self.cmb_codec = QComboBox()
        for value, text in codec_labels(self.ctx.env):
            self.cmb_codec.addItem(text, value)
        self.cmb_codec.currentIndexChanged.connect(self._on_codec_changed)

        self.cmb_speed = QComboBox()
        for name in X264_PRESETS:
            self.cmb_speed.addItem(_speed_label(name), name)

        self.cmb_tune = QComboBox()
        for value, text in TUNE_OPTIONS:
            self.cmb_tune.addItem(text, value)

        # 质量控制
        self.cmb_mode = QComboBox()
        for mode, text in MODE_ITEMS:
            self.cmb_mode.addItem(text, mode)
        self.cmb_mode.currentIndexChanged.connect(
            lambda i: self._quality_stack.setCurrentIndex(i) or self._update_estimate())

        # 滑杆以 0.1 为步进（内部整数 10~630 = 1.0~63.0）
        self.slider_crf = QSlider(Qt.Orientation.Horizontal)
        self.slider_crf.setRange(10, 630)
        self.slider_crf.setValue(230)
        self.spin_crf = QDoubleSpinBox()
        self.spin_crf.setRange(1.0, 63.0)
        self.spin_crf.setDecimals(1)
        self.spin_crf.setSingleStep(0.1)
        self.spin_crf.setValue(23.0)
        self.slider_crf.valueChanged.connect(
            lambda v: self.spin_crf.setValue(v / 10.0))
        self.spin_crf.valueChanged.connect(
            lambda v: self.slider_crf.setValue(round(v * 10)))
        self.slider_crf.valueChanged.connect(lambda _: self._update_estimate())

        crf_row = QWidget()
        crf_lay = QHBoxLayout(crf_row)
        crf_lay.setContentsMargins(0, 0, 0, 0)
        crf_lay.setSpacing(10)
        crf_lay.addWidget(self.slider_crf, 1)
        crf_lay.addWidget(self.spin_crf)
        self.crf_hint = label("", "Hint")
        self.crf_hint.setMinimumWidth(190)
        crf_lay.addWidget(self.crf_hint)

        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(100, 200000)
        self.spin_bitrate.setSingleStep(100)
        self.spin_bitrate.setValue(2000)
        self.spin_bitrate.setSuffix(" kbps")
        self.spin_bitrate.valueChanged.connect(lambda _: self._update_estimate())
        br_row = QWidget()
        br_lay = QHBoxLayout(br_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.addWidget(self.spin_bitrate)
        br_lay.addWidget(label("越高越清晰、文件越大；1080p 一般 2000–6000", "Hint"), 1)

        self.spin_size = QSpinBox()
        self.spin_size.setRange(1, 100000)
        self.spin_size.setValue(100)
        self.spin_size.setSuffix(" MB")
        self.spin_size.valueChanged.connect(lambda _: self._update_estimate())
        sz_row = QWidget()
        sz_lay = QHBoxLayout(sz_row)
        sz_lay.setContentsMargins(0, 0, 0, 0)
        sz_lay.addWidget(self.spin_size)
        sz_lay.addWidget(label("自动换算码率并二次编码，结果最接近目标", "Hint"), 1)

        self._quality_stack = QStackedWidget()
        self._quality_stack.addWidget(crf_row)
        self._quality_stack.addWidget(br_row)
        self._quality_stack.addWidget(sz_row)

        # 画面
        self.cmb_scale = QComboBox()
        for value, text in SCALE_OPTIONS:
            self.cmb_scale.addItem(text, value)
        self.cmb_scale.currentIndexChanged.connect(lambda _: self._update_estimate())

        self.cmb_fps = QComboBox()
        for value, text in FPS_OPTIONS:
            self.cmb_fps.addItem(text, value)

        # 音频
        self.cmb_audio = QComboBox()
        for value, text in AUDIO_CODEC_OPTIONS:
            self.cmb_audio.addItem(text, value)
        self.spin_audio_br = QSpinBox()
        self.spin_audio_br.setRange(32, 512)
        self.spin_audio_br.setValue(128)
        self.spin_audio_br.setSuffix(" kbps")
        self.spin_audio_br.valueChanged.connect(lambda _: self._update_estimate())
        audio_row = QWidget()
        au_lay = QHBoxLayout(audio_row)
        au_lay.setContentsMargins(0, 0, 0, 0)
        au_lay.setSpacing(8)
        au_lay.addWidget(self.cmb_audio, 1)
        au_lay.addWidget(self.spin_audio_br)
        self.cmb_audio.currentIndexChanged.connect(self._on_audio_changed)

        # 容器
        self.cmb_container = QComboBox()
        for c, text in CONTAINER_LABEL.items():
            self.cmb_container.addItem(text, c.value)
        self.cmb_container.currentIndexChanged.connect(lambda _: self._update_estimate())

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        two_col_rows = [
            ("编码器", self.cmb_codec, "编码速度", self.cmb_speed),
            ("质量模式", self.cmb_mode, "场景优化", self.cmb_tune),
            ("分辨率", self.cmb_scale, "帧率", self.cmb_fps),
            ("音频", audio_row, "输出格式", self.cmb_container),
        ]
        r = 0
        for l1, w1, l2, w2 in two_col_rows:
            grid.addWidget(label(l1), r, 0)
            grid.addWidget(w1, r, 1)
            grid.addWidget(label(l2), r, 2)
            grid.addWidget(w2, r, 3)
            r += 1
            if l1 == "质量模式":
                grid.addWidget(label("质量参数"), r, 0)
                grid.addWidget(self._quality_stack, r, 1, 1, 3)
                r += 1
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._panel = QWidget()
        self._panel.setLayout(grid)
        card.add(self._panel)

        card.add(label("CRF 数值越小画质越好、体积越大，范围 1–63、支持一位小数；"
                       "23 是通用起点，追求体积可用 26–28，追求画质用 18–20。"
                       "（H.264 上限 51，超出会自动钳制）", "Hint"))
        return card

    # ------------------------------------------------------------------ #
    # 联动
    # ------------------------------------------------------------------ #
    def _mode_index(self, index: int) -> None:
        self.cmb_mode.setCurrentIndex(index)
        self._quality_stack.setCurrentIndex(index)

    def _on_codec_changed(self, _index: int) -> None:
        codec = self.cmb_codec.currentData()
        is_hw = is_hw_encoder(resolve_codec(codec or "", self.ctx.env))
        self.cmb_speed.setEnabled(codec in ("libx264", "libx265"))
        self.cmb_tune.setEnabled(codec in ("libx264", "libx265"))
        if is_hw:
            self.cmb_mode.setItemText(0, "质量档位（硬件加速）")
        else:
            self.cmb_mode.setItemText(0, "恒定质量 CRF（推荐）")
        self._update_estimate()

    def _on_audio_changed(self, _index: int) -> None:
        codec = self.cmb_audio.currentData()
        self.spin_audio_br.setEnabled(codec not in ("copy", "none"))
        self._update_estimate()

    def _set_combo(self, combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    # 读取 / 估算
    # ------------------------------------------------------------------ #
    def current_settings(self) -> VideoSettings:
        mode = self.cmb_mode.currentData() or RateMode.CRF
        return VideoSettings(
            codec=self.cmb_codec.currentData() or "libx264",
            rate_mode=mode,
            crf=self.spin_crf.value(),
            bitrate_kbps=self.spin_bitrate.value(),
            preset=self.cmb_speed.currentData() or "medium",
            tune=self.cmb_tune.currentData() or "",
            scale_height=int(self.cmb_scale.currentData() or 0),
            fps=float(self.cmb_fps.currentData() or 0.0),
            audio_codec=self.cmb_audio.currentData() or "aac",
            audio_bitrate_kbps=self.spin_audio_br.value(),
            container=Container(self.cmb_container.currentData() or "mp4"),
        )

    def target_size_mb(self) -> float:
        if (self.cmb_mode.currentData() or RateMode.CRF) == RateMode.TARGET_SIZE:
            return float(self.spin_size.value())
        return 0.0

    def _update_estimate(self) -> None:
        bar = getattr(self, "bar", None)
        if bar is None:      # 构造过程中参数区可能先于操作栏初始化
            return
        settings = self.current_settings()
        crf = self.spin_crf.value()
        if settings.codec == "copy":
            self.crf_hint.setText("不重新编码，秒级完成")
        else:
            quality = ("画质很好" if crf <= 20 else "画质较好" if crf <= 24
                       else "体积优先" if crf <= 28 else "画质损失明显")
            note = ""
            if settings.codec == "libx264" and crf > 51:
                note = " · x264 上限 51，按 51 执行"
            self.crf_hint.setText(f"{quality}（CRF {crf:g}{note}）")

        paths = self.table.paths
        if not paths:
            self.bar.set_hint("还没有添加文件 —— 把视频拖进上面的列表即可开始")
            self.bar.button.setEnabled(False)
            return
        self.bar.button.setEnabled(self.ctx.env.ready)
        if not self.ctx.env.ready:
            self.bar.set_hint("未找到 ffmpeg，请到「设置」里指定 ffmpeg 路径", "Danger")
            return

        info = self.table.info_for(paths[0])
        est = estimate_output_size_mb(settings, info, self.target_size_mb())
        count = len(paths)
        prefix = f"共 {count} 个任务 · " if count > 1 else ""
        if est and info:
            src_mb = info.size / 1024 / 1024
            ratio = (est / src_mb * 100) if src_mb else 0
            self.bar.set_hint(
                f"{prefix}首个文件预计 {human_size(est * 1024 * 1024)}"
                f"（原 {human_size(info.size)}，约 {ratio:.0f}%）")
        else:
            self.bar.set_hint(f"{prefix}文件信息读取中…")

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _on_start(self) -> None:
        paths = self.table.paths
        if not paths:
            return
        if not self.ctx.env.ready:
            self.bar.set_hint("未找到 ffmpeg，请到「设置」里指定路径", "Danger")
            return

        settings = self.current_settings()
        target = self.target_size_mb()
        tasks = []
        for path in paths:
            info = self.table.info_for(path)
            tasks.append(build_compress_task(self.ctx, path, info, settings, target))

        self.ctx.queue.add_many(tasks)
        self.table.clear_all()
        self.bar.set_hint(f"已加入 {len(tasks)} 个任务，正在跳转到队列…")
        self.requestQueue.emit()


def _speed_label(name: str) -> str:
    table = {
        "ultrafast": "ultrafast（最快）",
        "superfast": "superfast",
        "veryfast": "veryfast",
        "faster": "faster",
        "fast": "fast",
        "medium": "medium（默认）",
        "slow": "slow",
        "slower": "slower",
        "veryslow": "veryslow（最慢最省）",
    }
    return table.get(name, name)
