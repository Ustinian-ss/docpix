"""生成 Windows 图标 ``packaging/docpix.ico``。

用 Pillow 画一个简单的品牌标：圆角蓝紫渐变底 + 白色「文档 + 图片」图形。
只需要在图标设计变化时手动跑一次：

    .\\.venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parent.parent / "packaging" / "docpix.ico"

TOP = (58, 123, 213)      # 蓝
BOTTOM = (124, 92, 214)   # 紫


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # 斜向渐变
            t = (x + y) / (2 * (size - 1)) if size > 1 else 0
            px[x, y] = tuple(
                round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)
            )
    return img


def _rounded_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size * 4 - 1, size * 4 - 1], radius=int(size * 4 * 0.22), fill=255
    )
    return mask.resize((size, size), Image.LANCZOS)


def _page_glyph(size: int) -> Image.Image:
    """白色纸张 + 图片图形（山 + 太阳）。"""
    s = size * 4
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 纸张
    m = int(s * 0.22)
    paper = [m, int(s * 0.16), s - m, s - int(s * 0.16)]
    d.rounded_rectangle(paper, radius=int(s * 0.05), fill=(255, 255, 255, 255))
    # 折角
    fold = int(s * 0.16)
    d.polygon(
        [
            (paper[2] - fold, paper[1]),
            (paper[2], paper[1] + fold),
            (paper[2] - fold, paper[1] + fold),
        ],
        fill=(206, 219, 240, 255),
    )

    # 图片图形：山 + 太阳
    inner = [paper[0] + int(s * 0.08), paper[1] + int(s * 0.14),
             paper[2] - int(s * 0.08), paper[3] - int(s * 0.09)]
    d.rounded_rectangle(inner, radius=int(s * 0.03), fill=(233, 240, 252, 255))
    d.polygon(
        [
            (inner[0] + int(s * 0.01), inner[3] - int(s * 0.02)),
            (inner[0] + int(s * 0.11), inner[1] + int(s * 0.12)),
            (inner[0] + int(s * 0.19), inner[3] - int(s * 0.02)),
        ],
        fill=(58, 123, 213, 255),
    )
    d.polygon(
        [
            (inner[0] + int(s * 0.13), inner[3] - int(s * 0.02)),
            (inner[0] + int(s * 0.20), inner[1] + int(s * 0.17)),
            (inner[0] + int(s * 0.27), inner[3] - int(s * 0.02)),
        ],
        fill=(124, 92, 214, 255),
    )
    d.ellipse(
        [inner[2] - int(s * 0.11), inner[1] + int(s * 0.03),
         inner[2] - int(s * 0.05), inner[1] + int(s * 0.09)],
        fill=(240, 180, 60, 255),
    )
    return layer.resize((size, size), Image.LANCZOS)


def build() -> Path:
    frames = []
    for size in SIZES:
        base = _gradient(size).convert("RGBA")
        base.putalpha(_rounded_mask(size))
        base.alpha_composite(_page_glyph(size))
        frames.append(base)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"图标已生成：{path}（{path.stat().st_size} 字节，{len(SIZES)} 种尺寸）")
    sys.exit(0)
