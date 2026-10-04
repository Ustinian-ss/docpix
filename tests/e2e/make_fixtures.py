"""生成端到端验证用的素材（图片 / PDF / Markdown / HEIC / OCR 样张）。

素材是**生成**的，不进版本库（`.gitignore` 忽略 ``tests/e2e/fixtures/``），
换台机器也能一键重建：

    .\\.venv\\Scripts\\python.exe tests\\e2e\\make_fixtures.py

默认输出到 ``tests/e2e/fixtures/``，可用 ``--out`` 改。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "fixtures"

OCR_WORDS = ("HELLO", "DOCPIX", "1234567890")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """找一个能用的 TrueType 字体（OCR 样张需要清晰字形）。"""
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_png(out: Path, name: str, size: tuple[int, int], bg, fg, label: str) -> Path:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, size[0] - 10, size[1] - 10], outline=fg, width=4)
    font = _font(48)
    d.text((40, 40), label, fill=fg, font=font)
    d.text((40, 120), f"{size[0]}x{size[1]}", fill=fg, font=font)
    path = out / name
    img.save(path, "PNG")
    return path


def make_ocr_sample(out: Path) -> Path:
    """白底黑字样张：OCR 后应当能检索到 HELLO / DOCPIX / 1234567890。"""
    img = Image.new("RGB", (1000, 420), (255, 255, 255))
    d = ImageDraw.Draw(img)
    font = _font(72)
    y = 60
    for word in OCR_WORDS:
        d.text((60, y), word, fill=(0, 0, 0), font=font)
        y += 110
    path = out / "ocr_sample.png"
    img.save(path, "PNG")
    return path


def make_scan_pdf(out: Path) -> Path:
    """无文字层的「扫描件」PDF：用来强制走 ocrmypdf 那条链路。

    图片输入走的是 ``tesseract <img> <out> pdf``，绕开了 ocrmypdf；
    要对 ocrmypdf（以及冻结后的 ``-m ocrmypdf`` 重入）做真实覆盖，
    必须喂一个**没有文字层**的 PDF 进去。
    """
    src = Image.open(out / "ocr_sample.png").convert("RGB")
    path = out / "scan.pdf"
    src.save(path, "PDF", resolution=150.0)
    return path


def make_pdf(out: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path = out / "report.pdf"
    cv = canvas.Canvas(str(path), pagesize=A4)
    pages = [
        ("Quarterly Report 2024", "docpix 端到端验证文档 第一页", "Page 1 of 3 - hello world"),
        ("第二章 项目概览", "这段文字用于验证 extract_text 输出。", "Page 2 of 3 - 中文内容"),
        ("第三章 结论", "全部引擎均已就绪。", "Page 3 of 3 - final page"),
    ]
    for title, cn, en in pages:
        cv.setFont("STSong-Light", 20)
        cv.drawString(72, 760, title)
        cv.setFont("STSong-Light", 14)
        cv.drawString(72, 700, cn)
        cv.drawString(72, 660, en)
        cv.setFont("Helvetica", 11)
        cv.drawString(72, 300, "The quick brown fox jumps over the lazy dog 0123456789")
        cv.showPage()
    cv.save()
    return path


def make_markdown(out: Path) -> Path:
    path = out / "notes.md"
    path.write_text(
        "# docpix 验证文档\n\n"
        "这是一个用于 **端到端** 验证的 Markdown 文件。\n\n"
        "## 列表\n\n"
        "- Pandoc\n"
        "- LibreOffice\n"
        "- Tesseract\n\n"
        "| 引擎 | 用途 |\n| --- | --- |\n| pandoc | 标记语言 |\n| qpdf | PDF 重写 |\n",
        encoding="utf-8",
    )
    return path


def make_heic(out: Path) -> Path | None:
    """HEIC 样张（验证 pillow-heif 与 libheif 本地库是否可用）。"""
    try:
        import pillow_heif
    except Exception as exc:  # pragma: no cover - 可选依赖
        print(f"  跳过 HEIC（pillow_heif 不可用：{exc}）")
        return None
    src = out / "img_a.png"
    img = Image.open(src).convert("RGB")
    path = out / "sample.heic"
    pillow_heif.from_pillow(img).save(path)
    return path


def build(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    made.append(make_png(out, "img_a.png", (800, 600), (255, 255, 255), (200, 30, 30), "DOCPIX-A"))
    made.append(make_png(out, "img_b.png", (1024, 768), (240, 248, 255), (30, 60, 200), "DOCPIX-B"))
    made.append(make_png(out, "img_c.png", (640, 480), (250, 250, 230), (20, 120, 20), "DOCPIX-C"))
    made.append(make_ocr_sample(out))
    made.append(make_scan_pdf(out))
    made.append(make_pdf(out))
    made.append(make_markdown(out))
    heic = make_heic(out)
    if heic:
        made.append(heic)
    return made


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="生成 docpix 端到端验证素材")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    args = parser.parse_args()

    made = build(args.out)
    print(f"素材目录：{args.out}")
    for p in made:
        print(f"  {p.name:<20} {p.stat().st_size:>8} 字节")
    print(f"共 {len(made)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
