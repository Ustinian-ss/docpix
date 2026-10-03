"""外部引擎测试：LibreOffice / Pandoc / Tesseract 真实调用。

用 ``-m "not engines"`` 可以跳过这些用例。引擎缺失时整组自动 skip。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import convert
from app.engines import ocr, office, pandoc, pdfops, registry, runner

pytestmark = pytest.mark.engines

needs_pandoc = pytest.mark.skipif(not pandoc.available(), reason="Pandoc 未安装")
needs_office = pytest.mark.skipif(not office.available(), reason="LibreOffice 未安装")
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="Tesseract/ocrmypdf 未就绪")


def test_engine_detection_lists_all_keys():
    detected = {e["key"]: e for e in registry.detect()}
    assert set(detected) == {"libreoffice", "pandoc", "qpdf", "tesseract"}
    for item in detected.values():
        assert item["license"] and item["homepage"]


def test_no_forbidden_engines_are_used():
    """许可红线：代码里不得真的调用 Ghostscript / PyMuPDF / Poppler。

    只检查「真的会执行」的形态（import 名、恰好等于被禁程序名的字符串常量），
    文档里提到这些名字（例如声明「不需要 Ghostscript」）不算违规。
    """
    import ast

    detected = {e["key"] for e in registry.detect()}
    assert not detected & {"ghostscript", "gs", "pymupdf", "fitz", "poppler", "pdftoppm"}

    banned_modules = {"fitz", "pymupdf", "ghostscript", "pdf2image", "pdftoppm", "poppler"}
    banned_exe = {"gs", "gswin32", "gswin64", "gswin32c", "gswin64c", "pdftoppm", "pdftotext",
                  "pdfinfo", "pdftocairo", "mutool", "magick"}

    for f in sorted((Path(__file__).parent.parent / "app").rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_modules, f"{f} 导入了 {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in banned_modules, f"{f} 导入了 {node.module}"
            elif isinstance(node, ast.List) and node.elts:
                # 只看命令数组的第一个元素（runner.run([...]) 的唯一入口形态）
                head = node.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    name = head.value.strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
                    name = name[:-4] if name.endswith(".exe") else name
                    assert name not in banned_exe, f"{f} 调用了被排除的程序：{head.value}"


@needs_pandoc
def test_pandoc_md_to_html(sample_md: Path, workdir: Path):
    out = pandoc.convert(sample_md, workdir / "out.html")
    assert out.stat().st_size > 0
    assert "<h1" in out.read_text(encoding="utf-8")


@needs_pandoc
def test_pandoc_refuses_pdf_output(sample_md: Path, workdir: Path):
    with pytest.raises(runner.EngineError):
        pandoc.convert(sample_md, workdir / "out.pdf")


@needs_office
def test_office_txt_to_pdf(workdir: Path):
    txt = workdir / "note.txt"
    txt.write_text("docpix office conversion test\n第二行\n", encoding="utf-8")
    pdf = office.to_pdf(txt, workdir / "out")
    assert pdf.is_file() and pdfops.page_count(pdf) >= 1


@needs_pandoc
@needs_office
def test_md_to_pdf_chain(sample_md: Path, workdir: Path):
    """核心链路：Pandoc 不能产 PDF，docpix 用 pandoc → html → LibreOffice 补齐。"""
    produced = convert.execute([sample_md], workdir / "out", "pdf")
    assert len(produced) == 1
    assert produced[0].name == "sample.pdf"
    assert pdfops.page_count(produced[0]) == 1


@needs_office
def test_docx_roundtrip_chain(sample_md: Path, workdir: Path):
    if not pandoc.available():
        pytest.skip("需要 Pandoc 生成 docx")
    docx = pandoc.convert(sample_md, workdir / "sample.docx")
    assert docx.stat().st_size > 0
    back = convert.execute([docx], workdir / "back", "pdf")
    assert pdfops.page_count(back[0]) >= 1


@needs_ocr
def test_ocr_languages_installed():
    langs = ocr.installed_languages()
    assert "eng" in langs
    status = ocr.status()
    assert status["available"] is True
    assert status["tessdata"]


@needs_ocr
def test_ocr_image_to_text(workdir: Path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 300), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        if Path(candidate).is_file():
            font = ImageFont.truetype(candidate, 64)
            break
    assert font is not None
    draw.text((40, 100), "DOCPIX OCR 2026", font=font, fill="black")
    png = workdir / "ocr.png"
    img.save(png)

    text = ocr.image_text(png, languages=["eng"])
    assert "DOCPIX" in text.upper().replace(" ", "")
    pdf = ocr.ocr_image_to_pdf(png, workdir / "ocr.pdf", languages=["eng"])
    assert pdfops.page_count(pdf) == 1


@needs_ocr
def test_ocr_pdf_makes_searchable(workdir: Path):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 300), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 64)
    draw.text((40, 100), "SEARCHABLE DOCPIX", font=font, fill="black")
    png = workdir / "scan.png"
    img.save(png)
    src = workdir / "scan.pdf"
    pdfops.images_to_pdf([png], src)
    assert pdfops.extract_text(src)[0]["text"].strip() == ""

    info = ocr.ocr_pdf(src, workdir / "scan_ocr.pdf", languages=["eng"], skip_text=True)
    assert info["pages"] == 1
    info_path = workdir / "scan_ocr.pdf"
    text = "".join(c["text"] for c in pdfops.extract_text(info_path))
    assert "DOCPIX" in text.upper()
    assert info_path.stat().st_size > 0
