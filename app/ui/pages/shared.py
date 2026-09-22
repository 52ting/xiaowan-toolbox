"""页面公用构件：页头、文件工具栏、预设卡片、输出目录行。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ...core.presets import Preset
from ...core.utils import VIDEO_EXTS, AUDIO_EXTS, open_in_file_manager
from ..style import palette
from ..widgets import FileTable, label


# --------------------------------------------------------------------------- #
# 页头
# --------------------------------------------------------------------------- #

class Page(QWidget):
    """所有页面的基类：统一标题 + 边距。"""

    def __init__(self, ctx, title: str, desc: str = "", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 18, 22, 16)
        self.root.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(label(title, "PageTitle"))
        if desc:
            head.addWidget(label(desc, "PageDesc"))
        self.root.addLayout(head)

    def add(self, widget_or_layout) -> None:
        if isinstance(widget_or_layout, QWidget):
            self.root.addWidget(widget_or_layout)
        else:
            self.root.addLayout(widget_or_layout)


# --------------------------------------------------------------------------- #
# 文件工具栏
# --------------------------------------------------------------------------- #

def _file_filter(exts: set[str], all_label: str = "媒体文件") -> str:
    patterns = " ".join(f"*{e}" for e in sorted(exts))
    return f"{all_label} ({patterns});;所有文件 (*)"


def make_file_toolbar(table: FileTable, exts: set[str] | None = None,
                      hint: str = "") -> QWidget:
    exts = exts or (VIDEO_EXTS | AUDIO_EXTS)
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    btn_files = QPushButton("添加文件")
    btn_dir = QPushButton("添加文件夹")
    btn_remove = QPushButton("移除所选")
    btn_clear = QPushButton("清空")

    lay.addWidget(btn_files)
    lay.addWidget(btn_dir)
    lay.addWidget(btn_remove)
    lay.addWidget(btn_clear)
    lay.addStretch(1)

    if hint:
        hint_label = label(hint, "Hint")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(hint_label)

    count = label("", "Hint")
    lay.addWidget(count)

    def refresh_count() -> None:
        n = table.rowCount()
        count.setText(f"共 {n} 个文件" if n else "")

    def on_files() -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            table, "选择文件", "", _file_filter(exts))
        if paths:
            table.add_paths(paths)

    def on_dir() -> None:
        path = QFileDialog.getExistingDirectory(table, "选择文件夹")
        if path:
            n = table.add_paths([path])
            if not n:
                count.setText("该文件夹里没有可处理的媒体文件")

    btn_files.clicked.connect(on_files)
    btn_dir.clicked.connect(on_dir)
    btn_remove.clicked.connect(table.remove_selected)
    btn_clear.clicked.connect(table.clear_all)
    table.filesChanged.connect(refresh_count)

    refresh_count()
    return row


# --------------------------------------------------------------------------- #
# 预设卡片
# --------------------------------------------------------------------------- #

def _wrap(text: str, fm, width: int, max_lines: int) -> list[str]:
    """按像素宽度折行：英文单词保持完整，中文逐字断行。"""
    import re

    tokens = re.findall(r"[A-Za-z0-9._\-/%]+|\S", text)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        candidate = cur + tok
        if fm.horizontalAdvance(candidate) > width and cur:
            lines.append(cur)
            cur = tok.lstrip()
            if len(lines) >= max_lines:
                break
        else:
            cur = candidate
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if lines and "".join(lines) != text:
        last = lines[-1]
        while last and fm.horizontalAdvance(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


class PresetFlowRow(QWidget):
    """预设卡片流式排布：按可用宽度自动决定每行张数，窗口窄时自动换行。"""

    heightChanged = Signal(int)

    CHIP_W, CHIP_H, GAP = 168, 66, 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips: list[QPushButton] = []
        self._cols = 0

    def addChip(self, chip: QPushButton) -> None:
        chip.setParent(self)
        chip.setFixedSize(QSize(self.CHIP_W, self.CHIP_H))
        chip.show()
        self._chips.append(chip)
        self._relayout(self.width())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(event.size().width())

    def _relayout(self, w: int) -> None:
        if not self._chips or w <= 0:
            return
        cols = max(1, (w + self.GAP) // (self.CHIP_W + self.GAP))
        if cols == self._cols:
            return
        self._cols = cols
        for i, chip in enumerate(self._chips):
            r, c = divmod(i, cols)
            chip.move(c * (self.CHIP_W + self.GAP),
                      r * (self.CHIP_H + self.GAP))
        rows = (len(self._chips) + cols - 1) // cols
        h = rows * self.CHIP_H + (rows - 1) * self.GAP
        self.setMinimumHeight(h)
        self.heightChanged.emit(h)


class PresetChip(QPushButton):
    """一键预设卡片：名称 + 标签 + 说明。"""

    def __init__(self, preset: Preset, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.setObjectName("PresetChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(preset.detail)
        self.setFixedSize(QSize(PresetFlowRow.CHIP_W, PresetFlowRow.CHIP_H))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        pal = palette()
        checked = self.isChecked()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pad = 11
        w = self.width() - pad * 2

        # 右上角标签（先量出宽度，给名称让位）
        tag_w = 0
        if self.preset.tagline:
            f2 = QFont()
            f2.setPointSize(8)
            f2.setBold(True)
            p.setFont(f2)
            fm2 = p.fontMetrics()
            tag_w = fm2.horizontalAdvance(self.preset.tagline) + 12

        # 名称（与标签同排时自动省略，避免重叠）
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        name = self.preset.name
        if tag_w:
            avail = w - tag_w - 8
            if fm.horizontalAdvance(name) > avail:
                name = fm.elidedText(name, Qt.TextElideMode.ElideRight,
                                     max(avail, int(w * 0.55)))
        p.setPen(QColor(pal["text"]))
        p.drawText(pad, 22, name)

        if self.preset.tagline:
            p.setFont(f2)
            chip = self._tag_rect(pad + w - tag_w)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal["accent_soft"]))
            p.drawRoundedRect(chip, 7, 7)
            p.setPen(QColor(pal["accent"]))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, self.preset.tagline)

        # 说明
        f3 = QFont()
        f3.setPointSize(8)
        p.setFont(f3)
        p.setPen(QColor(pal["text_dim"]))
        fm3 = p.fontMetrics()
        y = 38
        for line in _wrap(self.preset.detail, fm3, w, 2):
            p.drawText(pad, y, line)
            y += 14
        p.end()

    def _tag_rect(self, x: float):
        from PySide6.QtCore import QRectF

        f = QFont()
        f.setPointSize(8)
        f.setBold(True)
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(self.preset.tagline) + 14
        return QRectF(x, 10, w, 16)


# --------------------------------------------------------------------------- #
# 输出目录行
# --------------------------------------------------------------------------- #

class OutputRow(QWidget):
    """输出位置选择：与源文件相同 / 指定目录。"""

    changed = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(label("输出到"))
        self.combo = QComboBox()
        self.combo.addItem("与源文件相同目录", "source")
        self.combo.addItem("指定文件夹", "custom")
        self.combo.setMinimumWidth(150)
        lay.addWidget(self.combo)

        self.path_label = label("", "Hint")
        self.path_label.setMinimumWidth(200)
        lay.addWidget(self.path_label, 1)

        self.btn_pick = QPushButton("选择…")
        self.btn_open = QPushButton("打开目录")
        lay.addWidget(self.btn_pick)
        lay.addWidget(self.btn_open)

        self.combo.currentIndexChanged.connect(self._on_mode)
        self.btn_pick.clicked.connect(self._pick)
        self.btn_open.clicked.connect(self._open)
        self._sync()

    # ------------------------------------------------------------------ #
    def _sync(self) -> None:
        cfg = self.ctx.config
        self.combo.setCurrentIndex(0 if cfg.output_mode == "source" else 1)
        if cfg.output_mode == "source":
            self.path_label.setText("输出到源文件所在的文件夹")
            self.btn_open.setEnabled(False)
        else:
            self.path_label.setText(cfg.output_dir or "（未设置，请点「选择…」）")
            self.btn_pick.setEnabled(True)
            self.btn_open.setEnabled(bool(cfg.output_dir))

    def _on_mode(self, index: int) -> None:
        self.ctx.config.output_mode = self.combo.itemData(index) or "source"
        self.ctx.save_config()
        self._sync()
        self.changed.emit()

    def _pick(self) -> None:
        start = self.ctx.config.output_dir or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹", start)
        if path:
            self.ctx.config.output_dir = path
            self.ctx.save_config()
            self._sync()
            self.changed.emit()

    def _open(self) -> None:
        target = self.ctx.config.output_dir
        if target and Path(target).exists():
            open_in_file_manager(Path(target))
        else:
            self.path_label.setText("目录不存在，请重新选择")


class ActionBar(QWidget):
    """页面底部：说明文字 + 主操作按钮。"""

    def __init__(self, button_text: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.hint = label("", "Hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint, 1)
        self.button = QPushButton(button_text)
        self.button.setObjectName("Primary")
        self.button.setMinimumWidth(140)
        lay.addWidget(self.button)

    def set_hint(self, text: str, kind: str = "Hint") -> None:
        self.hint.setText(text)
        self.hint.setObjectName(kind)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)
