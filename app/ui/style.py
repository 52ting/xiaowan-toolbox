"""界面主题（浅色 / 深色两套 QSS）。"""

from __future__ import annotations

FONT_STACK = ('"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", '
              '"Segoe UI", "Helvetica Neue", Arial, sans-serif')
MONO_STACK = ('"SF Mono", "JetBrains Mono", Menlo, Consolas, "Courier New", monospace')

PALETTE = {
    "light": {
        "sidebar": "#F2F2F5",
        "content": "#FFFFFF",
        "panel": "#FAFAFC",
        "border": "#E3E3E8",
        "border_strong": "#D0D0D6",
        "text": "#1D1D1F",
        "text_dim": "#86868B",
        "text_faint": "#AEAEB2",
        "accent": "#007AFF",
        "accent_hover": "#0A84FF",
        "accent_soft": "#E7F0FF",
        "success": "#1FA34A",
        "danger": "#D93025",
        "warn": "#C77700",
        "track": "#EBEBF0",
        "row_alt": "#FBFBFD",
        "hover": "#F0F0F4",
    },
    "dark": {
        "sidebar": "#1C1C1E",
        "content": "#242426",
        "panel": "#2C2C2E",
        "border": "#3A3A3C",
        "border_strong": "#48484A",
        "text": "#F2F2F7",
        "text_dim": "#98989D",
        "text_faint": "#6E6E73",
        "accent": "#0A84FF",
        "accent_hover": "#3D9BFF",
        "accent_soft": "#1B3A5C",
        "success": "#30D158",
        "danger": "#FF453A",
        "warn": "#FF9F0A",
        "track": "#3A3A3C",
        "row_alt": "#2A2A2D",
        "hover": "#333336",
    },
}


def palette(theme: str | None = None) -> dict:
    """不传主题时自动读取当前应用主题（切主题后新绘制的元素立即生效）。"""
    if theme is None:
        theme = current_theme()
    return PALETTE.get(theme, PALETTE["light"])


def current_theme() -> str:
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            value = app.property("theme")
            if value:
                return str(value)
    except Exception:
        pass
    return "light"


def apply_theme(app, theme: str) -> None:
    """统一入口：设置应用级样式表并记录当前主题。"""
    app.setProperty("theme", theme)
    app.setStyleSheet(build_stylesheet(theme))


def _down_arrow_css(theme: str) -> str:
    """下拉箭头：运行时生成一张 chevron PNG 给 QSS 引用。

    之前用 CSS 边框画三角形，但 Qt 不给子控件尺寸时会把它
    渲染成一个小方块；真图标在任何缩放/主题下都稳定。
    """
    import tempfile
    from pathlib import Path

    cache = Path(tempfile.gettempdir()) / "XiaoWanToolbox"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        cache = Path(tempfile.gettempdir())
    path = cache / f"arrow_down_{theme}.png"
    url: str | None = None
    if not path.is_file():
        try:
            from PySide6.QtCore import QPointF, Qt
            from PySide6.QtGui import QColor, QImage, QPainter, QPen

            color = PALETTE.get(theme, PALETTE["light"])["text_dim"]
            img = QImage(28, 28, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(color))
            pen.setWidthF(3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(8, 11), QPointF(14, 17), QPointF(20, 11)])
            p.end()
            if not img.save(str(path)):
                path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    if path.is_file():
        url = path.resolve().as_posix()

    if url:
        return (f"QComboBox::down-arrow {{\n"
                f"    image: url({url});\n"
                f"    width: 17px;\n    height: 17px;\n    margin-right: 4px;\n}}")
    # 兜底：边框三角，显式零内容尺寸避免退化成方块
    return (f"QComboBox::down-arrow {{\n"
            f"    width: 0px;\n    height: 0px;\n"
            f"    border-left: 5px solid transparent;\n"
            f"    border-right: 5px solid transparent;\n"
            f"    border-top: 6px solid {p_text_dim(theme)};\n"
            f"    margin-right: 6px;\n}}")


def p_text_dim(theme: str) -> str:
    return PALETTE.get(theme, PALETTE["light"])["text_dim"]


def _radio_css(theme: str) -> str:
    """单选框指示器：运行时生成抗锯齿 PNG 给 QSS 引用。

    用 QSS 的 border+border-radius 画圆有天然缺陷：Qt 的宽高是内容区、
    边框向外扩，尺寸一变圆角半径就对不上，边缘还会留半像素缺口
    （用户看到的就是"缺了一角"）。真图标在任意缩放/主题下都干净。
    """
    import tempfile
    from pathlib import Path

    cache = Path(tempfile.gettempdir()) / "XiaoWanToolbox"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        cache = Path(tempfile.gettempdir())

    accent = PALETTE.get(theme, PALETTE["light"])["accent"]
    ring = PALETTE.get(theme, PALETTE["light"])["border_strong"]

    def draw(name: str, checked: bool) -> str | None:
        path = cache / f"{name}_{theme}.png"
        if not path.is_file():
            try:
                from PySide6.QtCore import Qt
                from PySide6.QtGui import QColor, QImage, QPainter, QPen

                S = 4  # 4x 超采样再缩小，边缘平滑
                size = 17 * S
                img = QImage(size, size, QImage.Format.Format_ARGB32)
                img.fill(Qt.GlobalColor.transparent)
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                cx = size / 2
                if checked:
                    # 实心圆盘 + 白色圆点（Windows 11 风格单选）
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(accent))
                    p.drawEllipse(cx - size / 2 + S * 0.5, S * 0.5,
                                  size - S, size - S)
                    dot = size * 0.36
                    p.setBrush(QColor("#FFFFFF"))
                    p.drawEllipse(cx - dot / 2, cx - dot / 2, dot, dot)
                else:
                    pen = QPen(QColor(ring))
                    pen.setWidthF(1.4 * S)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    inset = 0.9 * S
                    p.drawEllipse(inset, inset, size - 2 * inset,
                                  size - 2 * inset)
                p.end()
                small = img.scaled(17, 17, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                if not small.save(str(path)):
                    path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                return None
        return path.resolve().as_posix()

    off = draw("radio_off", False)
    on = draw("radio_on", True)
    if off and on:
        return (f"QRadioButton::indicator {{\n"
                f"    width: 17px;\n    height: 17px;\n"
                f"    background: transparent;\n    border: none;\n"
                f"    image: url({off});\n}}\n"
                f"QRadioButton::indicator:checked {{\n"
                f"    image: url({on});\n"
                f"    background: transparent;\n    border: none;\n}}\n")
    # 兜底：QSS 画圆（选中态总尺寸保持 17px，避免膨胀成方块）
    return (f"QRadioButton::indicator {{\n"
            f"    width: 15px;\n    height: 15px;\n"
            f"    border: 1px solid {ring};\n"
            f"    background: transparent;\n"
            f"    border-radius: 8px;\n}}\n"
            f"QRadioButton::indicator:checked {{\n"
            f"    width: 5px;\n    height: 5px;\n"
            f"    background: {accent};\n"
            f"    border: 6px solid {accent};\n"
            f"    border-radius: 8px;\n}}")


def build_stylesheet(theme: str = "light") -> str:
    p = palette(theme)
    down_arrow_css = _down_arrow_css(theme)
    radio_css = _radio_css(theme)
    return f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    outline: none;
}}

QWidget {{
    color: {p['text']};
    background: transparent;
}}

QMainWindow, #Root {{
    background: {p['content']};
}}

/* ---------------- 侧边栏 ---------------- */
#Sidebar {{
    background: {p['sidebar']};
    border-right: 1px solid {p['border']};
}}
#BrandTitle {{
    font-size: 15px;
    font-weight: 700;
    color: {p['text']};
}}
#BrandSub {{
    font-size: 11px;
    color: {p['text_dim']};
}}
#BrandBadge {{
    background: {p['accent']};
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 700;
    border-radius: 9px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
}}
#NavButton {{
    text-align: left;
    padding: 8px 12px;
    border: none;
    border-radius: 7px;
    background: transparent;
    color: {p['text']};
    font-size: 13.5px;
}}
#NavButton:hover {{
    background: {p['hover']};
}}
#NavButton:checked {{
    background: {p['accent']};
    color: #FFFFFF;
    font-weight: 600;
}}
#SidebarFoot {{
    color: {p['text_faint']};
    font-size: 11px;
}}
#EnvDot {{
    font-size: 11px;
}}
#EnvText {{
    font-size: 11px;
    color: {p['text_dim']};
}}

/* ---------------- 通用文本 ---------------- */
#PageTitle {{
    font-size: 20px;
    font-weight: 700;
}}
#PageDesc {{
    font-size: 12px;
    color: {p['text_dim']};
}}
#SectionTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {p['text']};
}}
#Hint {{
    font-size: 11.5px;
    color: {p['text_dim']};
}}
#Warn {{
    font-size: 12px;
    color: {p['warn']};
}}
#HintStrong {{
    font-size: 12px;
    color: {p['accent']};
    font-weight: 600;
}}
#Danger {{
    color: {p['danger']};
    font-size: 12px;
}}

/* ---------------- 卡片 ---------------- */
#Card {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: 10px;
}}

/* ---------------- 按钮 ---------------- */
QPushButton {{
    background: {p['panel']};
    border: 1px solid {p['border_strong']};
    border-radius: 7px;
    padding: 6px 14px;
    color: {p['text']};
}}
QPushButton:hover {{
    background: {p['hover']};
}}
QPushButton:pressed {{
    background: {p['track']};
}}
QPushButton:disabled {{
    color: {p['text_faint']};
    border-color: {p['border']};
    background: transparent;
}}
QPushButton#Primary {{
    background: {p['accent']};
    border: 1px solid {p['accent']};
    color: #FFFFFF;
    font-weight: 600;
    padding: 7px 20px;
}}
QPushButton#Primary:hover {{
    background: {p['accent_hover']};
}}
QPushButton#Primary:disabled {{
    background: {p['track']};
    border-color: {p['track']};
    color: {p['text_faint']};
}}
QPushButton#Ghost {{
    background: transparent;
    border: none;
    color: {p['accent']};
    padding: 4px 6px;
}}
QPushButton#Ghost:hover {{
    text-decoration: underline;
}}
QPushButton#DangerBtn {{
    color: {p['danger']};
    border-color: {p['border_strong']};
}}

/* ---------------- 预设卡片 ---------------- */
QPushButton#PresetChip {{
    text-align: left;
    padding: 0px;
    border: 1px solid {p['border']};
    border-radius: 9px;
    background: {p['panel']};
    min-width: 168px;
    max-width: 168px;
    min-height: 66px;
    max-height: 66px;
}}
QPushButton#PresetChip:hover {{
    border-color: {p['accent']};
    background: {p['accent_soft']};
}}
QPushButton#PresetChip:checked {{
    border: 2px solid {p['accent']};
    background: {p['accent_soft']};
}}

/* ---------------- 输入控件 ---------------- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QFontComboBox {{
    background: {p['content']};
    border: 1px solid {p['border_strong']};
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
    color: {p['text']};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {p['accent']};
}}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
    border: 1px solid {p['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
{down_arrow_css}
QComboBox QAbstractItemView {{
    background: {p['content']};
    border: 1px solid {p['border_strong']};
    border-radius: 6px;
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
    padding: 4px;
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}

/* ---------------- 滑块 ---------------- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {p['track']};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p['accent']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 1px solid {p['border_strong']};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {p['accent']};
}}

/* ---------------- 表格 ---------------- */
QTableWidget, QTableView {{
    background: {p['content']};
    border: 1px solid {p['border']};
    border-radius: 9px;
    gridline-color: transparent;
    selection-background-color: {p['accent_soft']};
    selection-color: {p['text']};
    alternate-background-color: {p['row_alt']};
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 8px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {p['accent_soft']};
    color: {p['text']};
}}
QHeaderView {{
    background: transparent;
}}
QHeaderView::section {{
    background: {p['panel']};
    color: {p['text_dim']};
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid {p['border']};
    font-size: 11.5px;
    font-weight: 600;
}}
QHeaderView::section:first {{
    border-top-left-radius: 9px;
}}
QHeaderView::section:last {{
    border-top-right-radius: 9px;
}}
QTableCornerButton::section {{
    background: {p['panel']};
    border: none;
}}

/* ---------------- 进度条 ---------------- */
QProgressBar {{
    background: {p['track']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {p['accent']};
    border-radius: 4px;
}}

/* ---------------- 日志 ---------------- */
QPlainTextEdit, QTextEdit {{
    background: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: 9px;
    font-family: {MONO_STACK};
    font-size: 11.5px;
    color: {p['text_dim']};
    padding: 8px;
    selection-background-color: {p['accent']};
    selection-color: #FFFFFF;
}}

/* ---------------- 滚动条 ---------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p['border_strong']};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p['text_faint']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p['border_strong']};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------------- 复选框 / 单选 ---------------- */
QCheckBox, QRadioButton {{
    spacing: 7px;
    color: {p['text']};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {p['border_strong']};
    background: {p['content']};
    border-radius: 4px;
}}
QCheckBox::indicator:checked {{
    background: {p['accent']};
    border-color: {p['accent']};
}}
{radio_css}

/* ---------------- 分组 / 标签页 ---------------- */
QGroupBox {{
    border: 1px solid {p['border']};
    border-radius: 9px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: {p['text_dim']};
    font-size: 12px;
}}

QTabWidget::pane {{
    border: 1px solid {p['border']};
    border-radius: 9px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    padding: 6px 14px;
    margin-right: 2px;
    border-radius: 7px;
    color: {p['text_dim']};
}}
QTabBar::tab:selected {{
    background: {p['panel']};
    color: {p['text']};
    font-weight: 600;
}}

/* ---------------- 分隔线 ---------------- */
QFrame#HLine {{
    background: {p['border']};
    max-height: 1px;
    border: none;
}}
QFrame#VLine {{
    background: {p['border']};
    max-width: 1px;
    border: none;
}}

/* ---------------- 状态徽标 ---------------- */
#Pill_ok {{
    background: {p['accent_soft']};
    color: {p['success']};
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
}}
#Pill_err {{
    background: {p['accent_soft']};
    color: {p['danger']};
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 11px;
}}
#StatValue {{
    font-size: 17px;
    font-weight: 700;
    color: {p['text']};
}}
#StatLabel {{
    font-size: 11px;
    color: {p['text_dim']};
}}
QToolTip {{
    background: {p['text']};
    color: {p['content']};
    border: none;
    border-radius: 5px;
    padding: 5px 8px;
}}
QSplitter::handle {{
    background: {p['border']};
}}

/* ---------------- 弹窗 ---------------- */
/* 全局 QWidget 规则是 background: transparent，QMessageBox 会落到
   系统原生调色板上——Windows 深色模式下对话框变黑，而文字仍用应用的
   主题色（浅色主题=深色字），就成了黑底黑字。这里显式接管。 */
QMessageBox, QInputDialog {{
    background: {p['content']};
}}
QMessageBox QLabel, QInputDialog QLabel {{
    background: transparent;
    color: {p['text']};
}}
QMessageBox QPushButton {{
    background: {p['sidebar']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 7px;
    padding: 7px 16px;
    min-width: 64px;
}}
QMessageBox QPushButton:hover {{
    background: {p['hover']};
    border-color: {p['accent']};
}}
QMessageBox QPushButton:default {{
    background: {p['accent']};
    color: #FFFFFF;
    border-color: {p['accent']};
    font-weight: 600;
}}

/* 右键菜单：和弹窗同款问题——不接管就会落到系统原生调色板上，
   Windows 深色模式下黑底配应用主题的深色字。 */
QMenu {{
    background: {p['content']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 9px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: {p['text']};
    padding: 7px 20px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {p['accent']};
    color: #FFFFFF;
}}
QMenu::item:disabled {{
    color: {p['text_faint']};
}}
QMenu::separator {{
    height: 1px;
    background: {p['border']};
    margin: 5px 10px;
}}
"""
