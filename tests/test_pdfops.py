"""PDF 引擎测试（pypdf / pikepdf / pypdfium2 / reportlab，不需要外部引擎）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.engines import pdfops, runner
from tests.conftest import make_pdf


def test_info_and_page_count(sample_pdf: Path):
    info = pdfops.info(sample_pdf)
    assert info["pages"] == 3
    assert info["encrypted"] is False
    assert pdfops.page_count(sample_pdf) == 3
    assert pdfops.is_encrypted(sample_pdf) is False


def test_parse_ranges_forms():
    assert pdfops.parse_ranges("1-3", 5) == [[0, 1, 2]]
    assert pdfops.parse_ranges("5", 5) == [[4]]
    assert pdfops.parse_ranges("-2", 4) == [[0, 1]]
    assert pdfops.parse_ranges("3-", 4) == [[2, 3]]
    assert pdfops.parse_ranges("1-2,4", 4) == [[0, 1], [3]]
    with pytest.raises(runner.EngineError):
        pdfops.parse_ranges("1-9", 3)
    with pytest.raises(runner.EngineError):
        pdfops.parse_ranges("abc", 3)
    with pytest.raises(runner.EngineError):
        pdfops.parse_ranges("", 3)


def test_merge_split_extract_delete_reorder(sample_pdf: Path, workdir: Path):
    merged = pdfops.merge([sample_pdf, sample_pdf], workdir / "merged.pdf")
    assert pdfops.page_count(merged) == 6

    each = pdfops.split(sample_pdf, workdir / "each", mode="each")
    assert len(each) == 3

    ranges = pdfops.split(sample_pdf, workdir / "ranges", mode="ranges", ranges="1-2,3")
    assert len(ranges) == 2

    halves = pdfops.split(sample_pdf, workdir / "half", mode="half")
    assert [pdfops.page_count(p) for p in halves] == [2, 1]

    steps = pdfops.split(sample_pdf, workdir / "n", mode="n", ranges=2)
    assert [pdfops.page_count(p) for p in steps] == [2, 1]

    extracted = pdfops.extract_pages(sample_pdf, workdir / "ex.pdf", "1,3")
    assert pdfops.page_count(extracted) == 2

    deleted = pdfops.delete_pages(sample_pdf, workdir / "del.pdf", "2")
    assert pdfops.page_count(deleted) == 2

    reordered = pdfops.reorder(sample_pdf, workdir / "ord.pdf", "3,1,2")
    assert pdfops.page_count(reordered) == 3

    with pytest.raises(runner.EngineError):
        pdfops.reorder(sample_pdf, workdir / "bad.pdf", "1,1,2")
    with pytest.raises(runner.EngineError):
        pdfops.delete_pages(sample_pdf, workdir / "empty.pdf", "1-3")


def test_rotate_and_compress(sample_pdf: Path, workdir: Path):
    rotated = pdfops.rotate(sample_pdf, workdir / "rot.pdf", 90)
    assert pdfops.page_count(rotated) == 3
    with pytest.raises(runner.EngineError):
        pdfops.rotate(sample_pdf, workdir / "rot2.pdf", 45)

    info = pdfops.compress(sample_pdf, workdir / "small.pdf", level="high")
    assert info["after"] == (workdir / "small.pdf").stat().st_size
    assert info["level"] == "high"
    with pytest.raises(runner.EngineError):
        pdfops.compress(sample_pdf, workdir / "x.pdf", level="nope")


def test_encrypt_decrypt_roundtrip(sample_pdf: Path, workdir: Path):
    locked = pdfops.encrypt(sample_pdf, workdir / "locked.pdf", user_password="pw123",
                            allow_print=False, allow_copy=False)
    assert pdfops.is_encrypted(locked) is True

    opened = pdfops.decrypt(locked, workdir / "open.pdf", "pw123")
    assert pdfops.is_encrypted(opened) is False

    with pytest.raises(runner.EngineError):
        pdfops.decrypt(locked, workdir / "open2.pdf", "wrong")

    with pytest.raises(runner.EngineError):
        pdfops.encrypt(sample_pdf, workdir / "nopw.pdf", user_password="")


def test_page_numbers_watermark_metadata(sample_pdf: Path, workdir: Path):
    numbered = pdfops.add_page_numbers(sample_pdf, workdir / "num.pdf", template="{page}/{total}",
                                       start_at=1)
    assert numbered.stat().st_size > 0

    marked = pdfops.add_watermark(sample_pdf, workdir / "wm.pdf", text="机密", opacity=0.2)
    assert marked.stat().st_size > 0

    meta = pdfops.set_metadata(sample_pdf, workdir / "meta.pdf", title="标题", author="docpix",
                               ignored="x")
    info = pdfops.info(meta)
    assert info["metadata"].get("Title") == "标题"
    assert info["metadata"].get("Author") == "docpix"


def test_text_extraction(sample_pdf: Path):
    chunks = pdfops.extract_text(sample_pdf)
    assert len(chunks) == 3
    assert "docpix" in chunks[0]["text"].lower()

    only_second = pdfops.extract_text(sample_pdf, pages="2")
    assert len(only_second) == 1 and only_second[0]["page"] == 2


def test_pdf_to_images_and_back(sample_pdf: Path, workdir: Path):
    images = pdfops.to_images(sample_pdf, workdir / "imgs", dpi=72, fmt="png")
    assert len(images) == 3
    assert all(p.suffix == ".png" and p.stat().st_size > 0 for p in images)

    back = pdfops.images_to_pdf(images, workdir / "back.pdf", page_size="fit")
    assert pdfops.page_count(back) == 3

    a4 = pdfops.images_to_pdf(images, workdir / "a4.pdf", page_size="A4", margin_mm=10)
    info = pdfops.info(a4)
    assert info["pages"] == 3
    assert round(info["page_size_mm"][0]) == 210


def test_render_preview(sample_pdf: Path):
    png = pdfops.render_preview(sample_pdf, page=2, width=400)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 100


def test_linearize_and_repair(sample_pdf: Path, workdir: Path):
    lin = pdfops.linearize(sample_pdf, workdir / "lin.pdf")
    assert pdfops.page_count(lin) == 3
    rep = pdfops.repair(sample_pdf, workdir / "rep.pdf")
    assert pdfops.page_count(rep) == 3


def test_merge_requires_inputs(workdir: Path):
    with pytest.raises(runner.EngineError):
        pdfops.merge([], workdir / "none.pdf")


def test_broken_pdf_reports_error(workdir: Path):
    bad = workdir / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    info = pdfops.info(bad)
    assert "error" in info or info.get("pages", 0) == 0


def test_make_pdf_helper_pages(workdir: Path):
    p = make_pdf(workdir / "five.pdf", pages=5)
    assert pdfops.page_count(p) == 5
