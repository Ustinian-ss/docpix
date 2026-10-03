"""PDF 处理引擎。

底层刻意只用宽松许可的库：

============  ==================  ==========================================
库            许可                 用途
============  ==================  ==========================================
pypdf         BSD-3-Clause        页面级操作、加密、合并、文本抽取
pikepdf       MPL-2.0             无损重写、压缩、线性化、图像降采样
pypdfium2     Apache-2.0 / BSD    渲染成位图（PDF → 图片）
reportlab     BSD-3-Clause        生成页码 / 水印叠加层
============  ==================  ==========================================

刻意**不**使用 Ghostscript(AGPL-3.0)、PyMuPDF(AGPL-3.0)、Poppler(GPL-2.0)，
以保证 docpix 自身可以保持 MIT 许可。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

import pikepdf
import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from .. import config
from . import registry, runner


# ===================================================================== 基础信息
def page_count(path: Path | str) -> int:
    with pikepdf.open(str(path)) as pdf:
        return len(pdf.pages)


def is_encrypted(path: Path | str) -> bool:
    try:
        with pikepdf.open(str(path)) as _:
            return False
    except pikepdf.PasswordError:
        return True


def info(path: Path | str) -> dict:
    """返回页数、体积、元数据、加密状态、页面尺寸。"""
    p = Path(path)
    out: dict = {"file": p.name, "size": p.stat().st_size if p.is_file() else 0}

    try:
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                out.update({"encrypted": True, "pages": 0, "metadata": {}})
                return out
        out["encrypted"] = False
        out["pages"] = len(reader.pages)
        md = reader.metadata or {}
        out["metadata"] = {k.lstrip("/"): str(v) for k, v in md.items()}
        if reader.pages:
            box = reader.pages[0].mediabox
            out["page_size_pt"] = [float(box.width), float(box.height)]
            out["page_size_mm"] = [round(float(box.width) / mm, 1), round(float(box.height) / mm, 1)]
    except Exception as exc:  # pragma: no cover - 损坏文件兜底
        out["error"] = str(exc)
    return out


# ================================================================= 合并 / 拆分
def merge(inputs: Iterable[Path | str], dst: Path | str) -> Path:
    """把多个 PDF 按给定顺序合并成一个。"""
    writer = PdfWriter()
    files = [Path(p) for p in inputs]
    if not files:
        raise runner.EngineError("合并至少需要一个 PDF 文件。")

    for f in files:
        reader = PdfReader(str(f))
        if reader.is_encrypted:
            reader.decrypt("")
        for page in reader.pages:
            writer.add_page(page)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def parse_ranges(spec: str, total: int) -> list[list[int]]:
    """解析 ``"1-3,5,8-"`` 这类页码表达式，返回 0 基索引分组。

    支持 ``1-3``（闭区间）、``5``（单页）、``8-``（到末尾）、``-3``（从头到 3）。
    多段之间用逗号分隔，每段会单独产出一个文件。
    """
    groups: list[list[int]] = []
    for chunk in str(spec).replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "-" in chunk:
                left, _, right = chunk.partition("-")
                start = int(left) if left.strip() else 1
                end = int(right) if right.strip() else total
            else:
                start = end = int(chunk)
        except ValueError:
            raise runner.EngineError(f"无法解析页码区间：{chunk!r}") from None
        if start < 1 or end > total or start > end:
            raise runner.EngineError(f"页码区间 {chunk!r} 超出范围（共 {total} 页）")
        groups.append(list(range(start - 1, end)))
    if not groups:
        raise runner.EngineError(f"无法解析页码表达式：{spec!r}")
    return groups


def split(
    src: Path | str,
    outdir: Path | str,
    *,
    mode: str = "each",
    ranges: str | None = None,
) -> list[Path]:
    """拆分 PDF。

    Args:
        mode: ``each`` 每页一个文件；``ranges`` 按 ``ranges`` 表达式分组；
              ``half`` 从中间一分为二；``n`` 每 N 页一个文件（写在 ranges 里）。
    """
    src_path = Path(src)
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(src_path))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    stem = src_path.stem

    if mode == "ranges":
        groups = parse_ranges(ranges or "", total)
        labels = [c.strip().replace("-", "_") for c in str(ranges).split(",") if c.strip()]
    elif mode == "each":
        groups = [[i] for i in range(total)]
        labels = [str(i + 1) for i in range(total)]
    elif mode == "half":
        mid = (total + 1) // 2
        groups = [list(range(0, mid)), list(range(mid, total))]
        labels = ["part1", "part2"]
    elif mode == "n":
        step = int(ranges or 1)
        if step < 1:
            raise runner.EngineError("每份页数必须 ≥ 1")
        groups = [list(range(i, min(i + step, total))) for i in range(0, total, step)]
        labels = [f"part{i + 1}" for i in range(len(groups))]
    else:
        raise runner.EngineError(f"未知的拆分模式：{mode}")

    produced: list[Path] = []
    for idx, pages in enumerate(groups):
        if not pages:
            continue
        label = labels[idx] if idx < len(labels) else str(idx + 1)
        dst = out_dir / f"{stem}_{label}.pdf"
        writer = PdfWriter()
        for pi in pages:
            writer.add_page(reader.pages[pi])
        with open(dst, "wb") as fh:
            writer.write(fh)
        produced.append(dst)

    if not produced:
        raise runner.EngineError("拆分未产生任何文件。")
    return produced


def extract_pages(src: Path | str, dst: Path | str, pages: str) -> Path:
    """按页码表达式抽取页面组成新 PDF。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    groups = parse_ranges(pages, total)

    writer = PdfWriter()
    for g in groups:
        for pi in g:
            writer.add_page(reader.pages[pi])

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def delete_pages(src: Path | str, dst: Path | str, pages: str) -> Path:
    """删除指定页，保留其余页。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    drop = {pi for g in parse_ranges(pages, total) for pi in g}

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i not in drop:
            writer.add_page(page)
    if len(writer.pages) == 0:
        raise runner.EngineError("删除后没有剩余页面，操作已取消。")

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def reorder(src: Path | str, dst: Path | str, order: str) -> Path:
    """按新的页序重排，例如 ``order="3,1,2"``。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    idx = [int(x) - 1 for x in str(order).replace("，", ",").split(",") if x.strip()]
    if sorted(idx) != list(range(total)):
        raise runner.EngineError(f"页序必须是 1..{total} 的一个排列。")

    writer = PdfWriter()
    for i in idx:
        writer.add_page(reader.pages[i])

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


# ===================================================================== 旋转
def rotate(src: Path | str, dst: Path | str, angle: int, pages: str | None = None) -> Path:
    """顺时针旋转页面（``angle`` 取 90 / 180 / 270，可为负）。"""
    if angle % 90 != 0:
        raise runner.EngineError("旋转角度必须是 90 的整数倍。")

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with pikepdf.open(str(src)) as pdf:
        targets = range(len(pdf.pages))
        if pages:
            groups = parse_ranges(pages, len(pdf.pages))
            targets = sorted({i for g in groups for i in g})
        for i in targets:
            pdf.pages[i].rotate(int(angle), relative=True)
        pdf.save(dst_path)
    return dst_path


# ================================================================ 加密 / 解密
def encrypt(
    src: Path | str,
    dst: Path | str,
    *,
    user_password: str,
    owner_password: str | None = None,
    allow_print: bool = True,
    allow_copy: bool = True,
    allow_modify: bool = False,
) -> Path:
    """给 PDF 加密码并设置权限位。"""
    if not user_password:
        raise runner.EngineError("打开密码不能为空。")

    from pypdf.constants import UserAccessPermissions as UAP

    # 注意：pypdf 的 UserAccessPermissions 没有 NONE 成员，权限位直接用整数拼。
    # 位 1/2（R1/R2）是保留位，必须保持 0。
    perms = 0
    if allow_print:
        perms |= int(UAP.PRINT) | int(UAP.PRINT_TO_REPRESENTATION)
    if allow_copy:
        perms |= int(UAP.EXTRACT) | int(UAP.EXTRACT_TEXT_AND_GRAPHICS)
    if allow_modify:
        perms |= (int(UAP.MODIFY) | int(UAP.ADD_OR_MODIFY)
                  | int(UAP.FILL_FORM_FIELDS) | int(UAP.ASSEMBLE_DOC))

    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(
        user_password=user_password,
        owner_password=owner_password or user_password,
        permissions_flag=perms,
    )

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def decrypt(src: Path | str, dst: Path | str, password: str = "") -> Path:
    """去掉 PDF 密码。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        if not reader.decrypt(password):
            raise runner.EngineError("密码错误，无法解密该 PDF。")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


# ===================================================================== 压缩
_PROFILES = {
    "low":     {"quality": 82, "max_dim": 3000, "recompress": False},
    "medium":  {"quality": 72, "max_dim": 2200, "recompress": True},
    "high":    {"quality": 55, "max_dim": 1600, "recompress": True},
    "extreme": {"quality": 40, "max_dim": 1100, "recompress": True},
}


def _downsample_images(pdf: pikepdf.Pdf, quality: int, max_dim: int) -> int:
    """把页面里的位图重新编码为更小的 JPEG，返回处理的图像数量。"""
    from PIL import Image
    from pikepdf import PdfImage

    touched = 0
    for page in pdf.pages:
        try:
            images = dict(page.images)
        except Exception:
            continue
        for _name, stream in images.items():
            try:
                pim = PdfImage(stream)
                if pim.image_mask:          # 软掩码单独处理，跳过
                    continue
                w, h = int(pim.width), int(pim.height)
                if w <= 0 or h <= 0:
                    continue
                pil = pim.as_pil_image()
                scale = min(1.0, max_dim / float(max(w, h)))
                if scale < 1.0:
                    pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
                if buf.tell() >= len(bytes(stream.read_raw_bytes())):
                    continue                # 压完反而更大就别换
                stream.write(buf.getvalue(), filter=pikepdf.Name("/DCTDecode"))
                stream.Width = pil.width
                stream.Height = pil.height
                stream.ColorSpace = pikepdf.Name("/DeviceRGB" if pil.mode == "RGB" else "/DeviceGray")
                stream.BitsPerComponent = 8
                for key in ("/SMask", "/Decode", "/DecodeParms"):
                    if key in stream:
                        del stream[key]
                touched += 1
            except Exception:
                continue
    return touched


def compress(
    src: Path | str,
    dst: Path | str,
    *,
    level: str = "medium",
    image_quality: int | None = None,
    max_image_dim: int | None = None,
) -> dict:
    """压缩 PDF。返回前后体积与处理的图像数量。"""
    profile = _PROFILES.get(level.lower())
    if profile is None:
        raise runner.EngineError(f"未知压缩档位：{level}；可选 {', '.join(_PROFILES)}")

    quality = image_quality if image_quality is not None else profile["quality"]
    max_dim = max_image_dim if max_image_dim is not None else profile["max_dim"]

    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    before = src_path.stat().st_size

    with pikepdf.open(src_path) as pdf:
        touched = 0
        if profile["recompress"]:
            touched = _downsample_images(pdf, quality, max_dim)
        pdf.save(
            dst_path,
            compress_streams=True,
            recompress_flate=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            linearize=True,
        )

    after = dst_path.stat().st_size
    return {
        "before": before,
        "after": after,
        "saved_ratio": round(1 - after / before, 4) if before else 0.0,
        "images_recompressed": touched,
        "level": level,
    }


def linearize(src: Path | str, dst: Path | str) -> Path:
    """用 qpdf 做线性化（Web 快速打开）。qpdf 缺失或失败时退回 pikepdf。"""
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    exe = registry.resolve("qpdf")
    if exe:
        try:
            runner.run([str(exe), "--linearize", "--object-streams=generate", str(src), str(dst_path)])
            return dst_path
        except runner.EngineError:
            pass  # 回退 pikepdf

    with pikepdf.open(str(src)) as pdf:
        pdf.save(dst_path, linearize=True)
    return dst_path


def repair(src: Path | str, dst: Path | str) -> Path:
    """尝试修复损坏的 PDF（优先用 qpdf，失败则用 pikepdf 的恢复模式重写）。"""
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    exe = registry.resolve("qpdf")
    if exe:
        try:
            # 注意：qpdf 没有 --replace-input=false 这种写法，传了会直接打帮助并退出 2
            runner.run([str(exe), "--qdf", "--object-streams=disable", str(src), str(dst_path)])
            return dst_path
        except runner.EngineError:
            pass

    try:
        with pikepdf.open(str(src), attempt_recovery=True) as pdf:
            pdf.save(dst_path)
    except Exception as exc:  # pikepdf 的异常类型很杂，统一转成 EngineError
        raise runner.EngineError(f"无法修复该 PDF：{exc}") from exc
    return dst_path


# ============================================================= 页码 / 水印
_FONT_CACHE: dict[str, str] = {}


def cjk_font() -> str:
    """注册一个支持中文的字体，返回字体名。

    优先嵌入系统里的微软雅黑 / 黑体（只读取 C 盘字体文件，不写入 C 盘）；
    都找不到时退回 reportlab 内置的 CID 中文字体 STSong-Light。
    """
    if "name" in _FONT_CACHE:
        return _FONT_CACHE["name"]

    candidates = [
        ("DocPixCJK", r"C:\Windows\Fonts\msyh.ttc", 0),
        ("DocPixCJK", r"C:\Windows\Fonts\msyhbd.ttc", 0),
        ("DocPixCJK", r"C:\Windows\Fonts\simhei.ttf", None),
        ("DocPixCJK", r"C:\Windows\Fonts\simsun.ttc", 0),
        ("DocPixCJK", r"C:\Windows\Fonts\Deng.ttf", None),
    ]
    for name, path, sub in candidates:
        try:
            if not Path(path).is_file():
                continue
            if sub is None:
                pdfmetrics.registerFont(TTFont(name, path))
            else:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=sub))
            _FONT_CACHE["name"] = name
            return name
        except Exception:
            continue

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _FONT_CACHE["name"] = "STSong-Light"
        return "STSong-Light"
    except Exception:
        _FONT_CACHE["name"] = "Helvetica"
        return "Helvetica"


def _overlay(width_pt: float, height_pt: float, draw) -> io.BytesIO:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(width_pt, height_pt))
    draw(c, width_pt, height_pt)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def add_page_numbers(
    src: Path | str,
    dst: Path | str,
    *,
    template: str = "{page} / {total}",
    position: str = "bottom-center",
    font_size: int = 10,
    margin_mm: float = 12.0,
    start_at: int = 1,
    color: tuple[float, float, float] = (0.2, 0.2, 0.2),
) -> Path:
    """给每一页叠加页码。``template`` 支持 {page} / {total} / {n}。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    writer = PdfWriter()
    font = cjk_font()
    margin = margin_mm * mm

    for i, page in enumerate(reader.pages):
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        label = (
            template.replace("{page}", str(start_at + i))
            .replace("{n}", str(start_at + i))
            .replace("{total}", str(total))
        )

        def draw(c, w=w, h=h, label=label):
            c.setFillColor(Color(*color))
            c.setFont(font, font_size)
            tw = c.stringWidth(label, font, font_size)
            if position.endswith("left"):
                x = margin
            elif position.endswith("right"):
                x = w - margin - tw
            else:
                x = (w - tw) / 2
            y = margin if position.startswith("bottom") else h - margin - font_size
            c.drawString(x, y, label)

        overlay = _overlay(w, h, draw)
        page.merge_page(PdfReader(overlay).pages[0])
        writer.add_page(page)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def add_watermark(
    src: Path | str,
    dst: Path | str,
    *,
    text: str,
    font_size: int = 48,
    opacity: float = 0.18,
    angle: float = 45.0,
    color: tuple[float, float, float] = (0.9, 0.1, 0.1),
    pages: str | None = None,
) -> Path:
    """叠加文字水印。"""
    from pypdf import Transformation

    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    targets = {pi for g in parse_ranges(pages, total) for pi in g} if pages else set(range(total))

    writer = PdfWriter()
    font = cjk_font()

    for i, page in enumerate(reader.pages):
        if i in targets:
            box = page.mediabox
            w, h = float(box.width), float(box.height)

            def draw(c, w=w, h=h):
                c.saveState()
                c.setFillColor(Color(*color), alpha=opacity)
                c.setFont(font, font_size)
                tw = c.stringWidth(text, font, font_size)
                c.translate(w / 2, h / 2)
                c.rotate(angle)
                c.drawString(-tw / 2, -font_size / 3, text)
                c.restoreState()

            overlay = _overlay(w, h, draw)
            page.merge_page(PdfReader(overlay).pages[0], over=True)
        writer.add_page(page)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def add_image_watermark(
    src: Path | str,
    dst: Path | str,
    image: Path | str,
    *,
    scale: float = 0.2,
    opacity: float = 0.35,
    position: str = "bottom-right",
    margin_mm: float = 10.0,
) -> Path:
    """叠加图片水印（PNG 透明通道会被保留）。"""
    from reportlab.lib.utils import ImageReader

    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    img_path = str(image)
    margin = margin_mm * mm

    for page in reader.pages:
        box = page.mediabox
        w, h = float(box.width), float(box.height)

        def draw(c, w=w, h=h):
            ir = ImageReader(img_path)
            iw, ih = ir.getSize()
            target_w = w * scale
            target_h = target_w * ih / iw
            if position.endswith("left"):
                x = margin
            elif position.endswith("right"):
                x = w - margin - target_w
            else:
                x = (w - target_w) / 2
            y = margin if position.startswith("bottom") else h - margin - target_h
            c.saveState()
            try:
                c.setFillAlpha(opacity)
            except Exception:
                pass
            c.drawImage(ir, x, y, width=target_w, height=target_h, mask="auto")
            c.restoreState()

        overlay = _overlay(w, h, draw)
        page.merge_page(PdfReader(overlay).pages[0], over=True)
        writer.add_page(page)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


# ============================================================ PDF ⇄ 图片
def to_images(
    src: Path | str,
    outdir: Path | str,
    *,
    dpi: int = 150,
    fmt: str = "png",
    pages: str | None = None,
    quality: int = 90,
) -> list[Path]:
    """把 PDF 每页渲染成图片。"""
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(src).stem
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"

    doc = pdfium.PdfDocument(str(src))
    total = len(doc)
    target = {pi for g in parse_ranges(pages, total) for pi in g} if pages else set(range(total))

    produced: list[Path] = []
    scale = dpi / 72.0
    for i in range(total):
        if i not in target:
            continue
        page = doc[i]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil()
        if fmt in ("jpeg", "jpg") and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        ext = "jpg" if fmt == "jpeg" else fmt
        out = out_dir / f"{stem}_p{i + 1:04d}.{ext}"
        save_kwargs = {"quality": quality} if fmt == "jpeg" else {}
        image.save(out, **save_kwargs)
        produced.append(out)
        page.close()
    doc.close()

    if not produced:
        raise runner.EngineError("PDF 转图片未产生任何文件。")
    return produced


def images_to_pdf(
    images: Iterable[Path | str],
    dst: Path | str,
    *,
    page_size: str = "fit",
    margin_mm: float = 0.0,
    quality: int = 92,
) -> Path:
    """把多张图片合成一个 PDF。

    ``page_size="fit"`` 表示每页尺寸等于图片本身；也可传 ``A4`` / ``Letter``。
    """
    from PIL import Image

    files = [Path(p) for p in images]
    if not files:
        raise runner.EngineError("合成 PDF 至少需要一张图片。")

    pages = []
    for f in files:
        img = Image.open(f)
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        pages.append(img)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if page_size and page_size.lower() != "fit":
        from reportlab.lib.pagesizes import A3, A4, A5, LETTER, legal

        sizes = {"a3": A3, "a4": A4, "a5": A5, "letter": LETTER, "legal": legal}
        size = sizes.get(page_size.lower())
        if size is None:
            raise runner.EngineError(f"未知页面尺寸：{page_size}")
        from reportlab.lib.units import mm as rl_mm
        from reportlab.lib.utils import ImageReader

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=size)
        margin = margin_mm * rl_mm
        for img in pages:
            c.setPageSize(size)
            avail_w = size[0] - 2 * margin
            avail_h = size[1] - 2 * margin
            ratio = min(avail_w / img.width, avail_h / img.height)
            w, h = img.width * ratio, img.height * ratio
            tmp = io.BytesIO()
            img.save(tmp, format="JPEG", quality=quality)
            tmp.seek(0)
            # reportlab 5 移除了 ImageIO，改用 ImageReader
            c.drawImage(ImageReader(tmp), (size[0] - w) / 2, (size[1] - h) / 2, width=w, height=h)
            c.showPage()
        c.save()
        dst_path.write_bytes(buf.getvalue())
        return dst_path

    first, rest = pages[0], pages[1:]
    first.save(dst_path, "PDF", resolution=150.0, save_all=True, append_images=rest, quality=quality)
    return dst_path


# ===================================================================== 文本
def extract_text(src: Path | str, *, pages: str | None = None) -> list[dict]:
    """抽取每页文本。返回 ``[{"page": 1, "text": "..."}]``。"""
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    total = len(reader.pages)
    target = {pi for g in parse_ranges(pages, total) for pi in g} if pages else set(range(total))

    out: list[dict] = []
    for i, page in enumerate(reader.pages):
        if i not in target:
            continue
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = f"[抽取失败] {exc}"
        out.append({"page": i + 1, "text": text})
    return out


def set_metadata(src: Path | str, dst: Path | str, **fields: str) -> Path:
    """写入 PDF 元数据。可传 title / author / subject / keywords / creator / producer。

    注意：pypdf 5+ 起 ``add_metadata`` 的键必须带前导斜杠（``/Title``），
    传裸名会直接抛 DeprecationError。
    """
    key_map = {
        "title": "/Title",
        "author": "/Author",
        "subject": "/Subject",
        "keywords": "/Keywords",
        "creator": "/Creator",
        "producer": "/Producer",
    }
    reader = PdfReader(str(src))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    meta = {
        key_map[k]: str(v)
        for k, v in fields.items()
        if k in key_map and v is not None and str(v).strip()
    }
    if meta:
        writer.add_metadata(meta)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as fh:
        writer.write(fh)
    return dst_path


def render_preview(src: Path | str, page: int = 1, *, width: int = 900) -> bytes:
    """渲染单页为 PNG 字节流，供前端预览。"""
    doc = pdfium.PdfDocument(str(src))
    try:
        index = max(0, min(page - 1, len(doc) - 1))
        pg = doc[index]
        scale = width / pg.get_width()
        bitmap = pg.render(scale=scale)
        image = bitmap.to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        pg.close()
        return buf.getvalue()
    finally:
        doc.close()


__all__ = [
    "info", "page_count", "is_encrypted", "merge", "split", "extract_pages",
    "delete_pages", "reorder", "rotate", "encrypt", "decrypt", "compress",
    "linearize", "repair", "add_page_numbers", "add_watermark",
    "add_image_watermark", "to_images", "images_to_pdf", "extract_text",
    "set_metadata", "render_preview", "parse_ranges", "cjk_font",
]
