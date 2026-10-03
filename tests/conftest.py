"""pytest 公共夹具。

注意：``client`` 必须是 session 作用域 —— ``app.main`` 的 lifespan 在退出时
会关闭任务线程池，如果每个用例都重建 TestClient，第二个用例就跑不起来了。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402

config.isolate_temp()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_image(path: Path, size=(320, 240), color=(200, 60, 60), mode="RGB") -> Path:
    from PIL import Image, ImageDraw

    img = Image.new(mode, size, color if mode != "RGBA" else color + (255,))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, size[0] - 20, size[1] - 20], outline=(0, 0, 0), width=3)
    draw.text((40, 40), "docpix", fill=(0, 0, 0))
    img.save(path)
    return path


@pytest.fixture
def sample_jpg(workdir: Path) -> Path:
    return _make_image(workdir / "sample.jpg")


@pytest.fixture
def sample_png(workdir: Path) -> Path:
    return _make_image(workdir / "sample.png", mode="RGBA", color=(20, 120, 220))


def make_pdf(path: Path, pages: int = 3, text: str = "docpix page") -> Path:
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A5)
    for i in range(pages):
        c.setFont("Helvetica", 14)
        c.drawString(40, 300, f"{text} {i + 1}")
        c.showPage()
    c.save()
    return path


@pytest.fixture
def sample_pdf(workdir: Path) -> Path:
    return make_pdf(workdir / "sample.pdf", pages=3)


@pytest.fixture
def sample_md(workdir: Path) -> Path:
    p = workdir / "sample.md"
    p.write_text("# 标题\n\n正文段落，带 **加粗**。\n\n| A | B |\n| - | - |\n| 1 | 2 |\n", encoding="utf-8")
    return p


@pytest.fixture
def upload_bytes(sample_jpg: Path):
    def _read(path: Path) -> tuple[str, bytes, str]:
        suffix = path.suffix.lower()
        mime = {"jpg": "image/jpeg", "png": "image/png", "pdf": "application/pdf"}.get(
            suffix.lstrip("."), "application/octet-stream"
        )
        return path.name, path.read_bytes(), mime

    return _read


__all__ = ["make_pdf", "io"]
