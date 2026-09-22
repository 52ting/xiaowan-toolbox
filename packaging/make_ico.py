#!/usr/bin/env python3
"""把 app/resources/icon.png 转成 Windows 打包用的多尺寸 icon.ico。

用法：
    python packaging/make_ico.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "app" / "resources"
SRC = HERE / "icon.png"
DST = Path(__file__).resolve().parent / "icon.ico"

# Windows 会在不同场合用到这些尺寸（任务栏 16/32、资源管理器 48/256、属性页 128）
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    if not SRC.is_file():
        print(f"找不到源图：{SRC}")
        return 1

    from PIL import Image

    img = Image.open(SRC).convert("RGBA")
    print(f"源图尺寸：{img.size[0]}×{img.size[1]}")

    # 非正方形先补成正方形，避免 ICO 里被拉伸
    if img.width != img.height:
        side = max(img.width, img.height)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = canvas

    img.save(DST, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"已生成：{DST}  ({DST.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
