"""图片引擎测试（Pillow / pillow-heif，不需要外部引擎）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.engines import imageops, runner


def size_of(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


def test_info(sample_jpg: Path):
    info = imageops.info(sample_jpg)
    assert info["width"] == 320 and info["height"] == 240
    assert info["format"] == "JPEG"
    assert info["size"] > 0
    assert info["megapixels"] > 0


def test_convert_to_png_and_webp(sample_jpg: Path, workdir: Path):
    png = imageops.convert(sample_jpg, workdir / "out.png", quality=90)
    assert png.is_file() and png.stat().st_size > 0
    with Image.open(png) as im:
        assert im.format == "PNG"

    webp = imageops.convert(sample_jpg, workdir / "out.webp", quality=80)
    with Image.open(webp) as im:
        assert im.format == "WEBP"
        assert im.size == (320, 240)


def test_convert_alpha_to_jpeg_flattens(sample_png: Path, workdir: Path):
    out = imageops.convert(sample_png, workdir / "flat.jpg", quality=85)
    with Image.open(out) as im:
        assert im.mode == "RGB"


def test_convert_unknown_target_raises(sample_jpg: Path, workdir: Path):
    with pytest.raises(runner.EngineError):
        imageops.convert(sample_jpg, workdir / "out.xyz")


def test_convert_missing_source_raises(workdir: Path):
    with pytest.raises(runner.EngineError):
        imageops.convert(workdir / "nope.jpg", workdir / "out.png")


@pytest.mark.parametrize("mode,expected", [
    ("fit", (160, 120)),
    ("contain", (200, 200)),
    ("cover", (200, 200)),
    ("fill", (200, 200)),        # fill 是 cover 的别名
    ("exact", (200, 100)),       # exact 是 stretch 的别名
    ("stretch", (200, 100)),
])
def test_resize_modes(sample_jpg: Path, workdir: Path, mode: str, expected: tuple[int, int]):
    out = imageops.resize(sample_jpg, workdir / f"{mode}.png", width=200 if mode != "fit" else 160,
                          height=100 if mode in ("exact", "stretch") else (200 if mode != "fit" else 120),
                          mode=mode)
    assert size_of(out) == expected


def test_resize_keeps_aspect_when_only_width(sample_jpg: Path, workdir: Path):
    out = imageops.resize(sample_jpg, workdir / "w.png", width=160, mode="fit")
    assert size_of(out) == (160, 120)


def test_resize_without_size_raises(sample_jpg: Path, workdir: Path):
    with pytest.raises(runner.EngineError):
        imageops.resize(sample_jpg, workdir / "x.png")


def test_compress_reports_sizes(sample_jpg: Path, workdir: Path):
    out = workdir / "small.jpg"
    info = imageops.compress(sample_jpg, out, quality=40, max_dim=100)
    assert info["after"] == out.stat().st_size
    assert max(size_of(out)) <= 100
    assert info["quality"] == 40


def test_crop_and_rotate_and_flip(sample_jpg: Path, workdir: Path):
    cropped = imageops.crop(sample_jpg, workdir / "crop.png", left=10, top=20, right=30, bottom=40)
    assert size_of(cropped) == (320 - 40, 240 - 60)

    rotated = imageops.rotate(sample_jpg, workdir / "rot.png", angle=90)
    assert size_of(rotated) == (240, 320)

    flipped = imageops.flip(sample_jpg, workdir / "flip.png", axis="vertical")
    assert size_of(flipped) == (320, 240)
    with pytest.raises(runner.EngineError):
        imageops.flip(sample_jpg, workdir / "bad.png", axis="diagonal")


def test_watermark_text_and_image(sample_jpg: Path, sample_png: Path, workdir: Path):
    out = imageops.watermark_text(sample_jpg, workdir / "wm.jpg", text="内部资料", opacity=0.5,
                                  font_size=24)
    assert out.stat().st_size > 0

    out2 = imageops.watermark_image(sample_jpg, workdir / "wm2.jpg", sample_png, scale=0.3, opacity=0.5)
    assert out2.stat().st_size > 0

    with pytest.raises(runner.EngineError):
        imageops.watermark_text(sample_jpg, workdir / "wm3.jpg", text="   ")


def test_combine(sample_jpg: Path, sample_png: Path, workdir: Path):
    out = imageops.combine([sample_jpg, sample_png], workdir / "combo.png", direction="vertical", gap=5)
    assert size_of(out)[1] == 240 + 240 + 5
    with pytest.raises(runner.EngineError):
        imageops.combine([sample_jpg], workdir / "combo2.png")


def test_strip_and_preview(sample_jpg: Path, workdir: Path):
    clean = imageops.strip(sample_jpg, workdir / "clean.jpg")
    assert clean.stat().st_size > 0
    data, mime = imageops.preview_bytes(sample_jpg, max_dim=100)
    assert mime in ("image/png", "image/jpeg") and len(data) > 0


def test_batch_convert(sample_jpg: Path, sample_png: Path, workdir: Path):
    out = workdir / "batch"
    produced = imageops.batch([sample_jpg, sample_png], out, "convert", params={"format": "png"})
    assert [p.name for p in produced] == ["sample_out.png", "sample_out_2.png"]
    assert all(p.suffix == ".png" and p.stat().st_size > 0 for p in produced)
    with pytest.raises(runner.EngineError):
        imageops.batch([sample_jpg], out, "nope", params={})
