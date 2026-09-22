#!/usr/bin/env python3
"""生成应用图标（icon.png）。在任意平台都能跑：

    python packaging/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget  # noqa: F401  (确保 Qt 完整初始化)

SIZE = 1024
HERE = Path(__file__).resolve().parents[1] / "app" / "resources"


def pick_cjk_font() -> str | None:
    """挑一个能真正渲染出「丸」(U+4E38) 的字体，找不到返回 None。"""
    from PySide6.QtGui import QFontDatabase, QFontMetrics

    for family in ("PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                   "Noto Sans CJK SC", "Source Han Sans SC", "SimHei",
                   "WenQuanYi Micro Hei", "Arial Unicode MS"):
        if family not in QFontDatabase.families():
            continue
        f = QFont(family)
        if QFontMetrics(f).inFontUcs4(0x4E38):
            return family
    return None


def main() -> int:
    app = QGuiApplication(sys.argv[:1])

    img_path = HERE / "icon.png"
    HERE.mkdir(parents=True, exist_ok=True)

    from PySide6.QtGui import QImage

    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    margin = 100
    rect = QRectF(margin, margin, SIZE - margin * 2, SIZE - margin * 2)
    radius = rect.width() * 0.225  # 类似 macOS Big Sur 图标的圆角

    # 背景渐变
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, QColor("#3D9BFF"))
    grad.setColorAt(1.0, QColor("#0A5BD3"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect, radius, radius)

    # 顶部高光
    hl = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.center().y()))
    hl.setColorAt(0.0, QColor(255, 255, 255, 70))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(hl)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.setClipPath(path)
    p.drawRect(rect.adjusted(0, 0, 0, rect.height() / 2))
    p.setClipping(False)

    # 中心圆（丸）
    circle = QRectF(0, 0, rect.width() * 0.42, rect.width() * 0.42)
    center = rect.center() - QPointF(0, rect.height() * 0.02)
    circle.moveCenter(center)
    cg = QRadialGradient(circle.center() + QPointF(-40, -40), circle.width())
    cg.setColorAt(0.0, QColor("#FFFFFF"))
    cg.setColorAt(1.0, QColor("#EAF3FF"))
    p.setBrush(cg)
    p.setPen(QPen(QColor(255, 255, 255, 160), 6))
    p.drawEllipse(circle)

    # 「丸」字（找不到合适字体时保留纯圆，同样成立）
    family = pick_cjk_font()
    if family:
        f = QFont(family)
        f.setPixelSize(int(circle.width() * 0.62))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#0A5BD3"))
        p.drawText(circle, Qt.AlignmentFlag.AlignCenter, "丸")
    else:
        inner = QRectF(0, 0, circle.width() * 0.52, circle.width() * 0.52)
        inner.moveCenter(circle.center())
        p.setBrush(QColor("#0A5BD3"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(inner)

    p.end()
    img.save(str(img_path), "PNG")
    print(f"图标已生成：{img_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
