"""程序入口。"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QIcon, QWheelEvent
from PySide6.QtWidgets import (QAbstractSlider, QAbstractSpinBox, QApplication,
                               QComboBox)

from . import __version__
from .context import AppContext
from .ui.main_window import MainWindow
from .ui.style import apply_theme


class WheelGuard(QObject):
    """禁止鼠标滚轮修改参数控件。

    滚轮落在下拉框 / 数值框 / 滑杆上时不再改变数值，
    而是把滚动转发给所属的 QScrollArea 视口（页面照常滚动）。
    注意：不能只转发给直接父级——普通 QWidget 默认忽略滚轮且
    手动 sendEvent 不会触发 Qt 的向上传播，页面会滚不动。
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QScrollBar, QScrollArea

        if event.type() == QEvent.Type.Wheel and isinstance(
                obj, (QComboBox, QAbstractSpinBox, QAbstractSlider)):
            # 滚动条一律放行：滚轮悬在滚动条上就该滚它；
            # 且拦截它会形成 视口 -> 滚动条 -> 视口 的死循环
            # （QScrollArea 处理视口滚轮时会再次派生给内部滚动条，
            #   而滚动条的 parentWidget 是容器控件，不是滚动区）
            if isinstance(obj, QScrollBar):
                return False
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QScrollArea):
                w = w.parentWidget()
            if w is not None:
                QApplication.sendEvent(w.viewport(), QWheelEvent(event))
            return True
        return False


def resource_path(*parts: str):
    from pathlib import Path

    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base).joinpath("app", "resources", *parts)
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / "resources" / Path(*parts)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    app = QApplication(argv)
    app.setApplicationName("小丸工具箱")
    app.setApplicationDisplayName("小丸工具箱")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("XiaoWanToolbox")
    # Fusion 让 QSS 在各平台表现一致，避免 macOS 原生样式吞掉自定义样式
    app.setStyle("Fusion")

    guard = WheelGuard()
    app.installEventFilter(guard)
    app._wheel_guard = guard  # 持有引用，防止被垃圾回收

    icon_path = resource_path("icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    ctx = AppContext()
    apply_theme(app, ctx.config.theme)

    window = MainWindow(ctx)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
