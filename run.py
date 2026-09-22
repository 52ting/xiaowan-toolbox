#!/usr/bin/env python3
"""小丸工具箱 · Mac 版 —— 启动脚本。

用法：
    python run.py

首次运行前请安装依赖：
    pip install -r requirements.txt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 让 Qt 在 Retina / 高分屏上正确渲染
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def _crash_report(exc: BaseException) -> None:
    """打包成无控制台的 exe 后，异常默认是"静默退出"。

    这里把堆栈落盘并在 Windows 上弹一个原生错误框，
    避免用户双击后"什么都没发生"却无从排查。
    """
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log: Path | None = None
    try:
        from app.core.utils import data_dir

        log = data_dir() / "crash.log"
        log.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    sys.stderr.write(text)
    if sys.platform.startswith("win"):
        try:
            import ctypes

            body = f"{text}\n\n日志已保存到：{log}" if log else text
            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                None, body[-1800:], "小丸工具箱 · 启动失败", 0x10
            )
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    try:
        from app.main import main as app_main
    except ImportError as exc:  # pragma: no cover
        print("依赖缺失：", exc)
        print("请先执行：pip install -r requirements.txt")
        return 1
    try:
        return app_main()
    except BaseException as exc:  # noqa: BLE001
        _crash_report(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
