"""声道下混页：5.1 / 7.1 等多声道 → 立体声 / 单声道。

小丸工具箱的经典痛点场景：下载的影视资源是 5.1 音轨，
在手机、笔记本、耳机上播放时对白又小又糊，背景音还特别吵。
这个页面就是把这步「下混」做成一键操作，视频画面直接复制不重编码。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from ...core.commands import (channel_roles, guess_channel_role, layout_label,
                              merge_layout_text, merge_roles,
                              merge_slot_order, resolve_merge_layout)
from ...core.pipeline import build_downmix_task, build_merge_downmix_task
from ...core.presets import (DOWNMIX_FORMATS, DOWNMIX_BITRATES,
                             DOWNMIX_PRESETS, DOWNMIX_PRESETS_BY_KEY,
                             DOWNMIX_SAMPLE_RATES, FORMAT_AUDIO_CODEC,
                             MERGE_LAYOUTS, ROLE_NAME, VIDEO_CONTAINERS,
                             DownmixSettings, estimate_audio_mb, format_size_mb)
from ...core.utils import AUDIO_EXTS, VIDEO_EXTS
from ..widgets import Card, FileTable, label
from .shared import (ActionBar, OutputRow, Page, PresetChip, PresetFlowRow,
                     make_file_toolbar)

TARGET_OPTIONS = [
    (2, "立体声 Stereo（2.0）"),
    (1, "单声道 Mono（1.0）"),
]

# 低音炮（LFE）处理方式：值 = (是否混入, 增益 dB)
LFE_OPTIONS = [
    ("drop", "丢弃（推荐）"),
    ("-12", "-12 dB 轻微混入"),
    ("-6", "-6 dB 混入"),
    ("-3", "-3 dB 较多混入"),
    ("0", "0 dB 全量混入"),
]

LOUDNESS_OPTIONS = [
    (-16.0, "-16 LUFS（通用）"),
    (-14.0, "-14 LUFS（流媒体）"),
    (-18.0, "-18 LUFS（偏轻）"),
    (-20.0, "-20 LUFS（最轻）"),
]


class DownmixPage(Page):
    requestQueue = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(
            ctx, "声道下混",
            "把 5.1 / 7.1 等多声道音轨转成双声道立体声（或单声道），"
            "画面原样复制不重编码；也可以把拆成多个单声道文件的素材合成一条。")
        self._loading = False
        self._notice: tuple[str, str] | None = None

        # ---------------- 文件 ---------------- #
        self.table = FileTable(ctx.env_provider, VIDEO_EXTS | AUDIO_EXTS)
        card_files = Card()
        card_files.add(make_file_toolbar(self.table, VIDEO_EXTS | AUDIO_EXTS))
        card_files.add(self._build_mode_row())
        card_files.add(self._build_merge_block())
        card_files.layout().addWidget(self.table, 1)
        self.root.addWidget(card_files, 1)

        # 底部操作栏先建：参数区构建时就会回调 _update_hint
        self.bar = ActionBar("开始下混")
        self.bar.button.clicked.connect(self._on_start)
        self._chips: dict[str, PresetChip] = {}

        self.root.addWidget(self._build_presets())
        self.root.addWidget(self._build_params())
        self.root.addWidget(OutputRow(ctx))
        self.root.addWidget(self.bar)

        self.table.filesChanged.connect(self._on_files_changed)
        self._select_preset(ctx.config.last_downmix_preset or "standard")
        self._restore_mode()

    # ------------------------------------------------------------------ #
    # 处理方式：逐个文件各自输出 / 多轨合并成一条
    # ------------------------------------------------------------------ #
    def _is_merge(self) -> bool:
        return self.radio_merge.isChecked()

    def _build_mode_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)
        lay.addWidget(label("处理方式"))

        self.radio_each = QRadioButton("逐个文件，各自输出")
        self.radio_each.setToolTip(
            "列表里每个文件独立下混：N 个文件产出 N 个结果。")
        self.radio_merge = QRadioButton("多轨合并成一条")
        self.radio_merge.setToolTip(
            "把列表里的文件当成同一条素材的各路声道\n"
            "（比如 5.1 拆出来的 前左 / 前右 / 中置 / 低音炮 / 后左 / 后右 六个文件），\n"
            "按从上到下的顺序拼成一条多声道再下混，最终只产出一个文件。")

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.radio_each)
        self._mode_group.addButton(self.radio_merge)
        lay.addWidget(self.radio_each)
        lay.addWidget(self.radio_merge)
        lay.addStretch(1)

        self.lbl_mode = label("", "Hint")
        lay.addWidget(self.lbl_mode)

        self.radio_each.toggled.connect(self._on_mode_toggled)
        return row

    def _build_merge_block(self) -> QWidget:
        self.merge_block = QWidget()
        outer = QVBoxLayout(self.merge_block)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label("声道布局"))

        self.cmb_layout = QComboBox()
        for value, text in MERGE_LAYOUTS:
            self.cmb_layout.addItem(text, value)
        self.cmb_layout.setMinimumWidth(290)
        self.cmb_layout.setToolTip(
            "文件按列表从上到下依次对应布局里的声道：\n"
            "第 1 个文件 = 前左，第 2 个 = 前右……")
        lay.addWidget(self.cmb_layout)

        self.btn_sort = QPushButton("按文件名排序")
        self.btn_sort.setToolTip(
            "用文件名里的 FL / FR / 前左 / 中置 等标记自动排好顺序")
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_up.setToolTip("把选中的文件往上挪一位")
        self.btn_down.setToolTip("把选中的文件往下挪一位")
        lay.addWidget(self.btn_sort)
        lay.addWidget(self.btn_up)
        lay.addWidget(self.btn_down)
        lay.addStretch(1)
        outer.addWidget(row)

        self.lbl_merge = label("", "Hint")
        self.lbl_merge.setWordWrap(True)
        outer.addWidget(self.lbl_merge)

        self.cmb_layout.currentIndexChanged.connect(self._on_layout_changed)
        self.btn_sort.clicked.connect(self._sort_by_name)
        self.btn_up.clicked.connect(lambda: self._nudge(-1))
        self.btn_down.clicked.connect(lambda: self._nudge(1))
        return self.merge_block

    def _restore_mode(self) -> None:
        merge = bool(self.ctx.config.downmix_merge)
        self.radio_merge.setChecked(merge)
        self.radio_each.setChecked(not merge)
        idx = self.cmb_layout.findData(self.ctx.config.downmix_merge_layout or "auto")
        if idx >= 0:
            self.cmb_layout.setCurrentIndex(idx)
        self.table.set_name_prefix(self._merge_prefix)
        self._apply_mode()

    def _on_mode_toggled(self, _checked: bool = False) -> None:
        merge = self._is_merge()
        self.ctx.config.downmix_merge = merge
        if merge and (self.cmb_format.currentData() or "auto") == "auto":
            # 合并的源是一堆单声道文件，「跟随原文件」没有意义，落到 M4A
            self._set_combo(self.cmb_format, "m4a")
        self.ctx.save_config()
        self._apply_mode()

    def _apply_mode(self) -> None:
        merge = self._is_merge()
        self.merge_block.setVisible(merge)
        self.lbl_mode.setText(
            "列表里的文件会合成为一条，只产出 1 个结果" if merge
            else "每个文件各产出一个结果")
        # 合并时列表本身就是「声道顺序」，尽量让六行全部露出来
        self.table.setMinimumHeight(215 if merge else 160)
        self.table.set_name_prefix(self._merge_prefix)
        self._update_hint()

    def _merge_prefix(self, path_str: str, row: int):
        """文件名列的前缀 = 这个文件会被当成哪条声道。

        前缀说的是「槽位」而不是文件名里的标记 —— 槽位才是真正决定结果的。
        两者对不上时整行标黄，提醒用户点「按文件名排序」或手动挪。
        """
        if not self._is_merge():
            return ""
        roles = self._merge_roles()
        if row >= len(roles):
            return ""
        role = roles[row]
        zh = ROLE_NAME.get(role, "")
        prefix = f"{role} {zh} · " if zh else f"{role} · "
        guessed = guess_channel_role(Path(path_str).stem, roles)
        if guessed and guessed != role:
            return prefix, "warn"
        return prefix

    def _merge_roles(self) -> list[str]:
        count = len(self.table.paths)
        if count <= 0:
            return []
        return merge_roles(self.cmb_layout.currentData() or "auto", count)

    def _on_layout_changed(self, _index: int = 0) -> None:
        self.ctx.config.downmix_merge_layout = self.cmb_layout.currentData() or "auto"
        self.ctx.save_config()
        self.table.refresh_names()
        self._update_hint()

    def _on_files_changed(self) -> None:
        # 文件增删/换序都可能改变「哪个文件是哪条声道」，前缀要跟着重画
        self._notice = None
        if self._is_merge():
            self.table.refresh_names()
        self._update_hint()

    def _nudge(self, delta: int) -> None:
        if not self.table.move_selected(delta):
            self._set_merge_hint("先选中要挪动的文件（可多选）再点上下移", "Danger")

    def _sort_by_name(self) -> None:
        paths = self.table.paths
        roles = self._merge_roles()
        if len(paths) < 2 or not roles:
            self._set_merge_hint("先添加文件，并选好声道布局", "Danger")
            return

        guessed = [guess_channel_role(p.stem, roles) for p in paths]
        unknown = [p.name for p, g in zip(paths, guessed) if not g]
        if unknown:
            shown = "、".join(unknown[:3]) + ("…" if len(unknown) > 3 else "")
            self._set_merge_hint(
                f"有 {len(unknown)} 个文件名里认不出声道标记（{shown}），"
                f"请用「上移 / 下移」手动排", "Danger")
            return

        repeated = sorted({r for r in guessed if guessed.count(r) > 1})
        if repeated:
            self._set_merge_hint(
                "文件名里的声道标记有重复：" + "、".join(repeated) + "，请手动排",
                "Danger")
            return

        missing = [r for r in roles if r not in guessed]
        if missing:
            self._set_merge_hint(
                "文件名里缺少 " + " / ".join(missing) + "，对不上所选布局，请手动排",
                "Danger")
            return

        order = [guessed.index(r) for r in roles]
        if order == list(range(len(paths))):
            self._set_merge_hint("顺序本来就是对的，无需调整", "Hint")
            return
        if self.table.reorder(order):
            self._set_merge_hint("已按文件名里的声道标记排好顺序", "Hint")

    def _set_merge_label(self, text: str, kind: str = "Hint") -> None:
        self.lbl_merge.setText(text)
        self.lbl_merge.setObjectName(kind)
        self.lbl_merge.style().unpolish(self.lbl_merge)
        self.lbl_merge.style().polish(self.lbl_merge)

    def _set_merge_hint(self, text: str, kind: str = "Hint") -> None:
        """用户点了排序/挪动之后的临时提示，会一直留到文件列表再变为止。"""
        self._notice = (text, kind)
        self._set_merge_label(text, kind)

    def _merge_problem(self, paths: list[Path]) -> str:
        """阻断合并的问题说明；没有问题时返回空串。

        只有 1 个文件不算错误——点「开始下混」会自动按普通下混处理。
        """
        count = len(paths)
        if count <= 1:
            return ""
        if not resolve_merge_layout(self.cmb_layout.currentData() or "auto", count):
            return f"当前 {count} 个文件对不上任何一种声道布局，请在上方手动选择"
        for path in paths:
            info = self.table.info_for(path)
            if info is None:
                continue
            if info.error:
                return f"读不出「{path.name}」：{info.error}"
            if info.ok and not info.audios:
                return f"「{path.name}」里没有音频流"
        return ""

    def _merge_notes(self, paths: list[Path], roles: list[str]) -> list[str]:
        """不阻断执行、但值得提醒的情况。"""
        notes: list[str] = []
        durations = []
        for path in paths:
            info = self.table.info_for(path)
            durations.append(info.duration if info and info.duration else 0.0)
        known = [d for d in durations if d > 0]
        if len(known) == len(durations) and known and (
                max(known) - min(known)) > 0.05:
            notes.append(
                f"各路时长不一致（{min(known):.1f}s ~ {max(known):.1f}s），"
                f"已按最长的补齐，否则会被最短的那路截断")

        multi = []
        for path in paths:
            info = self.table.info_for(path)
            if info and info.audios and info.audios[0].channels > 1:
                multi.append(path.name)
        if multi:
            notes.append(f"其中 {len(multi)} 个不是单声道，会自动折成单声道")

        mismatch = sum(
            1 for row, path in enumerate(paths)
            if row < len(roles)
            and guess_channel_role(path.stem, roles)
            and guess_channel_role(path.stem, roles) != roles[row])
        if mismatch:
            notes.append(
                f"标黄的 {mismatch} 个文件与文件名里的声道标记对不上，"
                f"可点「按文件名排序」或手动挪")
        return notes

    def _render_merge_hint(self, paths: list[Path], problem: str = "") -> None:
        if self._notice:
            self._set_merge_label(*self._notice)
            return
        count = len(paths)
        if count == 0:
            self._set_merge_label(
                "把拆开的单声道文件一起拖进来（如 前左 / 前右 / 中置 / 低音炮 / "
                "后左 / 后右 六个文件），就能合成一条立体声", "Hint")
            return
        if count == 1:
            self._set_merge_label(
                "只拖了 1 个文件——合并至少需要 2 个单声道文件；"
                "现在点「开始下混」会按普通下混处理这个文件", "Warn")
            return
        layout = resolve_merge_layout(self.cmb_layout.currentData() or "auto", count)
        if not layout:
            self._set_merge_label(
                f"当前 {count} 个文件对不上任何一种声道布局，请在上方手动选择",
                "Danger")
            return
        roles = merge_roles(self.cmb_layout.currentData() or "auto", count)
        text = "文件按从上到下依次对应 —— " + merge_layout_text(layout, count)
        notes = self._merge_notes(paths, roles)
        if notes:
            text += "；" + "；".join(notes)
        self._set_merge_label(text, "Hint")

    # ------------------------------------------------------------------ #
    # 一键方案
    # ------------------------------------------------------------------ #
    def _build_presets(self) -> QWidget:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        lay.addWidget(label("下混方案", "SectionTitle"))

        flow = PresetFlowRow()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(PresetFlowRow.CHIP_H + 2)
        flow.heightChanged.connect(lambda h: scroll.setFixedHeight(h + 2))

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for preset in DOWNMIX_PRESETS:
            chip = PresetChip(preset)
            chip.clicked.connect(
                lambda _=False, key=preset.key: self._select_preset(key))
            self._group.addButton(chip)
            self._chips[preset.key] = chip
            flow.addChip(chip)

        scroll.setWidget(flow)
        lay.addWidget(scroll)
        return holder

    def _select_preset(self, key: str) -> None:
        preset = DOWNMIX_PRESETS_BY_KEY.get(key)
        if not preset:
            preset = DOWNMIX_PRESETS_BY_KEY["standard"]
            key = "standard"
        chip = self._chips.get(key)
        if chip:
            chip.setChecked(True)
        s = preset.settings
        self._loading = True
        try:
            self._set_combo(self.cmb_target, s.target_channels)
            self._set_gain(self.slider_center, self.spin_center, s.center_gain_db)
            self._set_gain(self.slider_surround, self.spin_surround,
                           s.surround_gain_db)
            lfe_key = "drop" if not s.include_lfe else f"{s.lfe_gain_db:g}"
            self._set_combo(self.cmb_lfe, lfe_key)
            self.chk_clip.setChecked(s.prevent_clip)
            self.chk_compress.setChecked(s.dialog_compress)
            self.chk_loud.setChecked(s.normalize_loudness)
            self._set_combo(self.cmb_loud, s.loudness_lufs)
            self.ctx.config.last_downmix_preset = key
            self.ctx.save_config()
        finally:
            self._loading = False
        self._sync_enabled()
        self._update_hint()

    def _clear_preset(self) -> None:
        """手动改过参数后，取消方案卡片的高亮，避免误导。"""
        self._group.setExclusive(False)
        for chip in self._chips.values():
            chip.setChecked(False)
        self._group.setExclusive(True)

    # ------------------------------------------------------------------ #
    # 参数
    # ------------------------------------------------------------------ #
    def _build_params(self) -> QWidget:
        card = Card()
        card.add(label("下混参数", "SectionTitle"))

        self.cmb_target = QComboBox()
        for value, text in TARGET_OPTIONS:
            self.cmb_target.addItem(text, value)
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)

        self.slider_center, self.spin_center = self._gain_pair(-12.0, 6.0, -3.0)
        self.slider_surround, self.spin_surround = self._gain_pair(-12.0, 6.0, -3.0)

        self.cmb_lfe = QComboBox()
        for value, text in LFE_OPTIONS:
            self.cmb_lfe.addItem(text, value)
        self.cmb_lfe.setToolTip(
            "低音炮轨道（LFE）单独存放低频效果。耳机与笔记本放不出来，\n"
            "混进来只会白占动态，所以默认丢弃。")

        self.cmb_format = QComboBox()
        for value, text, _tip in DOWNMIX_FORMATS:
            self.cmb_format.addItem(text, value)
        self.cmb_format.currentIndexChanged.connect(self._on_format_changed)

        self.cmb_bitrate = QComboBox()
        for br in DOWNMIX_BITRATES:
            self.cmb_bitrate.addItem(f"{br} kbps", int(br))
        # 默认对齐 FORMAT_AUDIO_CODEC 里 m4a 的 256kbps：
        # 影视下混里 256 比 192 的环绕细节明显更干净，体积只多 1/3
        self.cmb_bitrate.setCurrentIndex(DOWNMIX_BITRATES.index("256"))

        self.cmb_rate = QComboBox()
        for value, text in DOWNMIX_SAMPLE_RATES:
            self.cmb_rate.addItem(text, value)

        # 三个开关
        self.chk_clip = QCheckBox("防止削波")
        self.chk_clip.setChecked(True)
        self.chk_clip.setToolTip(
            "多路声道叠加后峰值可能超过 0dB，硬削波是不可逆的失真。\n"
            "开启后末尾会加一道限幅器，代价是约 5 毫秒延迟（听不出来）。")

        self.chk_compress = QCheckBox("对白动态压缩")
        self.chk_compress.setToolTip(
            "把忽大忽小的音量收一收，小声台词听得清、爆炸声不炸耳。")

        self.chk_loud = QCheckBox("响度标准化")
        self.chk_loud.setToolTip("按 EBU R128 标准统一整体响度，批量处理时各集音量一致。")
        self.cmb_loud = QComboBox()
        for value, text in LOUDNESS_OPTIONS:
            self.cmb_loud.addItem(text, value)

        left_gain = self._labeled_pair("中置（人声）", self.slider_center,
                                       self.spin_center)
        right_gain = self._labeled_pair("环绕（声场）", self.slider_surround,
                                        self.spin_surround)

        loud_row = QWidget()
        loud_lay = QHBoxLayout(loud_row)
        loud_lay.setContentsMargins(0, 0, 0, 0)
        loud_lay.setSpacing(8)
        loud_lay.addWidget(self.chk_loud)
        loud_lay.addWidget(self.cmb_loud, 1)

        switches = QWidget()
        sw_lay = QHBoxLayout(switches)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.setSpacing(18)
        sw_lay.addWidget(self.chk_clip)
        sw_lay.addWidget(self.chk_compress)
        sw_lay.addWidget(loud_row, 1)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.addWidget(label("目标声道"), 0, 0)
        grid.addWidget(self.cmb_target, 0, 1)
        grid.addWidget(label("输出格式"), 0, 2)
        grid.addWidget(self.cmb_format, 0, 3)
        grid.addWidget(label("中置（人声）"), 1, 0)
        grid.addWidget(left_gain, 1, 1)
        grid.addWidget(label("环绕（声场）"), 1, 2)
        grid.addWidget(right_gain, 1, 3)
        grid.addWidget(label("低音炮 LFE"), 2, 0)
        grid.addWidget(self.cmb_lfe, 2, 1)
        grid.addWidget(label("输出采样率"), 2, 2)
        grid.addWidget(self.cmb_rate, 2, 3)
        grid.addWidget(label("音频码率"), 3, 0)
        grid.addWidget(self.cmb_bitrate, 3, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        holder = QWidget()
        holder.setLayout(grid)
        card.add(holder)
        card.add(switches)
        card.add(label(
            "中置增益控制人声大小，环绕增益控制空间感与背景细节；"
            "±0dB 表示原样、负值表示衰减。"
            "源文件本来就是立体声时会自动跳过下混，只做格式转换；"
            "多轨合并时这些增益同样作用于合成后的声道。", "Hint", wrap=True))

        # 任何手动改动都取消方案卡片的高亮，避免显示与实际参数对不上
        for widget in (self.chk_clip, self.chk_compress):
            widget.toggled.connect(lambda _: self._on_manual_change())
        self.chk_loud.toggled.connect(lambda _: self._sync_enabled())
        self.chk_loud.toggled.connect(lambda _: self._on_manual_change())
        for combo in (self.cmb_lfe, self.cmb_bitrate, self.cmb_rate):
            combo.currentIndexChanged.connect(lambda _: self._on_manual_change())

        self._on_format_changed()
        return card

    def _gain_pair(self, lo: float, hi: float, value: float):
        """滑杆 + 数值框联动（滑杆内部按 0.1 步进存整数）。"""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(lo * 10), int(hi * 10))
        slider.setValue(int(value * 10))
        slider.setMinimumWidth(96)

        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(1)
        spin.setSingleStep(0.5)
        spin.setValue(value)
        spin.setSuffix(" dB")
        spin.setMinimumWidth(84)

        slider.valueChanged.connect(lambda v: spin.setValue(v / 10.0))
        spin.valueChanged.connect(lambda v: slider.setValue(round(v * 10)))
        slider.valueChanged.connect(lambda _: self._on_manual_change())
        return slider, spin

    @staticmethod
    def _labeled_pair(text: str, slider: QSlider, spin: QDoubleSpinBox) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(slider, 1)
        lay.addWidget(spin)
        return row

    @staticmethod
    def _set_gain(slider: QSlider, spin: QDoubleSpinBox, value: float) -> None:
        slider.setValue(round(value * 10))
        spin.setValue(value)

    def _on_manual_change(self) -> None:
        if self._loading:
            return
        self._clear_preset()
        self._update_hint()

    def _on_target_changed(self) -> None:
        self._sync_enabled()
        self._on_manual_change()

    def _on_format_changed(self) -> None:
        self._sync_enabled()
        self._on_manual_change()

    def _sync_enabled(self) -> None:
        """无损格式不需要码率；单声道时环绕/低音炮仍有意义，故只锁码率。"""
        from ...core.presets import FORMAT_AUDIO_CODEC

        fmt = self.cmb_format.currentData() or "auto"
        codec = FORMAT_AUDIO_CODEC.get(fmt, ("aac", 192))[0] if fmt != "auto" else ""
        lossless = fmt in ("flac", "wav") or codec in ("flac", "pcm_s16le")
        self.cmb_bitrate.setEnabled(not lossless)
        self.cmb_loud.setEnabled(self.chk_loud.isChecked())

    # ------------------------------------------------------------------ #
    # 读取 / 提示
    # ------------------------------------------------------------------ #
    def current_settings(self) -> DownmixSettings:
        lfe_key = self.cmb_lfe.currentData() or "drop"
        include_lfe = lfe_key != "drop"
        return DownmixSettings(
            target_channels=int(self.cmb_target.currentData() or 2),
            center_gain_db=self.spin_center.value(),
            surround_gain_db=self.spin_surround.value(),
            include_lfe=include_lfe,
            lfe_gain_db=float(lfe_key) if include_lfe else -6.0,
            dialog_compress=self.chk_compress.isChecked(),
            normalize_loudness=self.chk_loud.isChecked(),
            loudness_lufs=float(self.cmb_loud.currentData() or -16.0),
            prevent_clip=self.chk_clip.isChecked(),
            container=self.cmb_format.currentData() or "auto",
            audio_bitrate_kbps=int(self.cmb_bitrate.currentData() or 192),
            sample_rate=int(self.cmb_rate.currentData() or 0),
        )

    def _set_combo(self, combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _size_note(self, duration_s: float) -> str:
        """给出成品体积预估。

        「输出怎么只有源文件的几十分之一」是最常见的疑问——体积完全由
        码率和格式决定（192kbps 立体声 90 分钟就是 125MB 左右），
        跟声道数、混音质量无关，所以直接把预期写出来。
        """
        fmt = self.cmb_format.currentData() or "auto"
        if not duration_s or fmt in ("", "auto") or fmt in VIDEO_CONTAINERS:
            return ""
        codec, default_br = FORMAT_AUDIO_CODEC.get(fmt, ("aac", 192))
        br = int(self.cmb_bitrate.currentData() or default_br)
        target = int(self.cmb_target.currentData() or 2)
        mb = estimate_audio_mb(duration_s, codec, br, channels=target,
                               sample_rate=int(self.cmb_rate.currentData() or 48000))
        text = format_size_mb(mb)
        return f"，成品约 {text}" if text else ""

    def _update_hint(self) -> None:
        bar = getattr(self, "bar", None)
        if bar is None:
            return
        merge = self._is_merge()
        paths = self.table.paths

        def refresh_merge(problem: str = "") -> None:
            if merge:
                self._render_merge_hint(paths, problem)

        if not paths:
            bar.set_hint("还没有添加文件 —— 把 5.1 / 7.1 的影视文件拖进来即可")
            bar.button.setEnabled(False)
            refresh_merge()
            return
        if not self.ctx.env.ready:
            bar.set_hint("未找到 ffmpeg，请到「设置」里指定路径", "Danger")
            bar.button.setEnabled(False)
            refresh_merge()
            return

        if merge:
            problem = self._merge_problem(paths)
            refresh_merge(problem)
            if problem:
                bar.set_hint(problem, "Danger")
                bar.button.setEnabled(False)
                return
            layout = resolve_merge_layout(
                self.cmb_layout.currentData() or "auto", len(paths))
            target = int(self.cmb_target.currentData() or 2)
            target_name = "立体声" if target == 2 else "单声道"
            extra = ["响度标准化"] if self.chk_loud.isChecked() else []
            if self.chk_compress.isChecked():
                extra.append("动态压缩")
            tail = f"（{'、'.join(extra)}）" if extra else ""
            durs = []
            for path in paths:
                item = self.table.info_for(path)
                if item and item.duration:
                    durs.append(item.duration)
            bar.set_hint(
                f"把 {len(paths)} 个文件按顺序合成一条 {layout}，"
                f"再下混为{target_name}{tail} —— 最终只产出 1 个文件"
                f"{self._size_note(max(durs) if durs else 0.0)}")
            bar.button.setEnabled(True)
            return

        bar.button.setEnabled(True)

        info = self.table.info_for(paths[0])
        prefix = f"共 {len(paths)} 个任务 · " if len(paths) > 1 else ""
        target = int(self.cmb_target.currentData() or 2)
        target_name = "立体声" if target == 2 else "单声道"

        if info and info.audios:
            a = info.audios[0]
            roles = channel_roles(a.channel_layout, a.channels)
            if len(roles) > target:
                extra = []
                if a.channels > 2:
                    extra.append(f"{a.channels} 声道")
                if self.chk_loud.isChecked():
                    extra.append("响度标准化")
                if self.chk_compress.isChecked():
                    extra.append("动态压缩")
                tail = f"（{'、'.join(extra)}）" if extra else ""
                bar.set_hint(
                    f"{prefix}检测到 {layout_label(a.channel_layout, a.channels)}"
                    f" → 将下混为{target_name}{tail}"
                    f"{self._size_note(info.duration or 0.0)}")
            elif a.channels:
                bar.set_hint(
                    f"{prefix}源已是{layout_label(a.channel_layout, a.channels)}，"
                    f"无需下混，仅按所选格式输出"
                    f"{self._size_note(info.duration or 0.0)}")
            else:
                bar.set_hint(f"{prefix}正在读取声道信息…")
        elif info and info.error:
            bar.set_hint(f"{prefix}{info.error}", "Danger")
        else:
            bar.set_hint(f"{prefix}正在读取文件信息…")

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _verify_merge_order(self, paths: list[Path], roles: list[str]) -> bool:
        """开始前用文件名声道标记核对行序。

        返回 True 表示继续执行。当每个文件都能认出声道标记、标记互不
        重复且刚好覆盖所有槽位时，顺序是可判定的——若与槽位不符，
        弹窗让用户一键纠正；拒绝纠正则取消本次执行（宁可不做也不做错）。
        """
        guessed = [guess_channel_role(p.stem, roles) for p in paths]
        if any(not g for g in guessed):
            return True                      # 有文件认不出标记，无从核对
        if len(set(guessed)) != len(guessed):
            return True                      # 标记有重复，不强拦
        if set(guessed) != set(roles):
            return True                      # 覆盖不了所有槽位，不强拦
        order = [guessed.index(r) for r in roles]
        if order == list(range(len(paths))):
            return True                      # 顺序本来就对
        wrong = []
        for row, (path, g) in enumerate(zip(paths, guessed)):
            want = roles.index(g)
            if want != row:
                zh = ROLE_NAME.get(g, g)
                wrong.append(f"第 {row + 1} 行「{path.name}」是 {g} {zh}，"
                             f"应放在第 {want + 1} 行")
        shown = "\n".join(wrong[:6]) + ("……" if len(wrong) > 6 else "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("声道顺序与文件名不符")
        box.setText(
            "合并顺序决定每个文件被当成哪条声道。\n当前顺序和文件名里的"
            f"声道标记对不上（{len(wrong)} 处）：\n\n{shown}")
        box.setInformativeText(
            "顺序错了会导致对白跑到一边、环绕声代替主声道。\n"
            "要按文件名标记自动纠正后再开始吗？")
        fix = box.addButton("自动纠正并开始", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not fix:
            return False
        self.table.reorder(order)
        self.table.refresh_names()
        self._set_merge_hint("已按文件名里的声道标记自动纠正顺序", "Hint")
        return True

    def _on_start(self) -> None:
        paths = self.table.paths
        if not paths or not self.ctx.env.ready:
            return
        settings = self.current_settings()

        if self._is_merge() and len(paths) > 1:
            roles = self._merge_roles()
            if len(roles) != len(paths):
                self.bar.set_hint(
                    "声道数与文件数对不上，请检查声道布局与文件列表", "Danger")
                return
            if not self._verify_merge_order(paths, roles):
                return
            infos = [self.table.info_for(p) for p in paths]
            task = build_merge_downmix_task(
                self.ctx, paths, infos, roles, settings)
            if task is None:
                self.bar.set_hint(
                    "无法构建合并任务：请确认每个文件都有音频流", "Danger")
                return
            tasks = [task]
        else:
            # 单个文件不存在"合并"可言——即使停在合并模式，
            # 也必须走普通下混，否则会被当成 1 路单声道塌成单声道
            if self._is_merge():
                self.bar.set_hint("单个文件无需合并，已按普通下混处理")
            tasks = [build_downmix_task(self.ctx, path, self.table.info_for(path),
                                        settings)
                     for path in paths]

        self.ctx.queue.add_many(tasks)
        self.table.clear_all()
        self.bar.set_hint(f"已加入 {len(tasks)} 个任务，正在跳转到队列…")
        self.requestQueue.emit()
