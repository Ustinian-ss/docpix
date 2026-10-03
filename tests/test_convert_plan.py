"""链路规划器测试（纯逻辑，不需要任何外部引擎）。"""

from __future__ import annotations

import pytest

from app import convert
from app.engines import pandoc


def ops(plan: convert.Plan) -> list[str]:
    return [s.op for s in plan.steps]


def test_md_to_pdf_goes_through_html():
    """Pandoc 不能直接产 PDF，必须走 pandoc → html → LibreOffice → pdf。"""
    p = convert.plan("a.md", "pdf")
    assert ops(p) == ["pandoc", "office"]
    assert [s.target for s in p.steps] == ["html", "pdf"]
    assert "pandoc → html → LibreOffice → pdf" in p.warning


def test_docx_to_pdf_is_single_office_hop():
    p = convert.plan("a.docx", "pdf")
    assert ops(p) == ["office"]
    assert p.steps[0].target == "pdf"


def test_pdf_to_md_uses_office_then_pandoc():
    p = convert.plan("a.pdf", "md")
    assert ops(p) == ["office", "pandoc"]
    assert p.steps[0].target == "docx"
    assert p.warning


def test_pdf_to_text_uses_extractor():
    p = convert.plan("a.pdf", "txt")
    assert ops(p) == ["pdf_text"]


def test_pdf_to_image():
    p = convert.plan("a.pdf", "png")
    assert ops(p) == ["pdf_to_image"]


def test_image_to_pdf_and_image_to_image():
    assert ops(convert.plan("a.png", "pdf")) == ["image_to_pdf"]
    p = convert.plan("a.png", "webp")
    assert ops(p) == ["image"]
    assert p.steps[0].target == "webp"


def test_same_extension_is_copy():
    assert ops(convert.plan("a.PDF", "pdf")) == ["copy"]


def test_norm_ext_aliases():
    assert convert.norm_ext(".JPEG") == "jpg"
    assert convert.norm_ext("photo.TIF") == "tiff"
    assert convert.norm_ext(r"F:\tmp\a.docx") == "docx"
    assert convert.norm_ext("remarkdown.md") == "md"


def test_pandoc_output_formats_exclude_spreadsheets():
    """回归：Pandoc 没有 xlsx/csv/tsv writer，绝不能规划成 pandoc 直转。"""
    assert "xlsx" not in convert.PANDOC_OUT
    assert "csv" not in convert.PANDOC_OUT
    assert "tsv" not in convert.PANDOC_OUT
    assert "xlsx" not in pandoc.FORMAT_TO_EXT


def test_spreadsheet_targets_go_through_libreoffice():
    assert "office" in ops(convert.plan("docx", "xlsx"))
    assert "office" in ops(convert.plan("csv", "xlsx"))
    assert "office" in ops(convert.plan("xlsx", "csv"))
    # md → xlsx：pandoc 转 html，再由 LibreOffice 落成 xlsx
    assert ops(convert.plan("md", "xlsx")) == ["pandoc", "office"]


def test_unsupported_combination_raises():
    with pytest.raises(convert.PlanError):
        convert.plan("png", "docx")
    with pytest.raises(convert.PlanError):
        convert.plan("", "pdf")


def test_reachable_targets_and_matrix():
    targets = convert.reachable_targets("md")
    assert "pdf" in targets and "docx" in targets and "html" in targets
    matrix = convert.matrix()
    assert "md" in matrix and "pdf" in matrix["md"]
    # 图片目标不应出现在 markdown 的目标里
    assert "png" not in matrix["md"]


def test_capability_summary_shape():
    caps = convert.capability_summary()
    for key in ("matrix", "office_targets", "pandoc_targets", "image_output",
                "pdf_ops", "image_ops", "ocr_output_types"):
        assert key in caps
    assert {"merge", "split", "compress"} <= set(caps["pdf_ops"])
    assert {"convert", "resize", "watermark_text"} <= set(caps["image_ops"])
