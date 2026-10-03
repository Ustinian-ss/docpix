"""图片处理引擎（Pillow + pillow-heif）。

许可说明
--------
* Pillow —— MIT-CMU
* pillow-heif —— BSD-3-Clause（其内部动态链接的 libheif / libde265 为
  LGPL-3.0，本项目只通过 PyPI 依赖它、不随仓库分发其二进制，因此不影响
  docpix 自身的 MIT 许可）。

能力
----
读：jpg/jpeg/png/webp/avif/heic/heif/bmp/gif/tif/tiff/ico/jp2/jxl/ppm/pgm
写：jpg/png/webp/avif/heic/bmp/gif/tiff/ico
其它：缩放（fit/contain/cover/stretch）、压缩、裁剪、旋转、翻转、文字水印、
图片水印、拼接、去元数据、EXIF 自动转向。

所有函数都只在 ``dst`` 上写入，不修改源文件；失败时抛
:class:`~app.engines.runner.EngineError`（带可读中文说明）。
"""

from __future__ import annotations

import inspect
import io
import math
import uuid
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import runner

# --------------------------------------------------------------------- 常量
#: 扩展名 → 规范化的内部格式名
_EXT_TO_FMT = {
    "jpg": "JPEG", "jpeg": "JPEG", "jpe": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "avif": "AVIF",
    "heic": "HEIF", "heif": "HEIF",
    "bmp": "BMP",
    "gif": "GIF",
    "tif": "TIFF", "tiff": "TIFF",
    "ico": "ICO",
    "jp2": "JPEG2000",
    "jxl": "JXL",
    "ppm": "PPM", "pgm": "PPM",
}

#: 支持写出（保存）的格式
OUTPUT_FORMATS: tuple[str, ...] = (
    "jpg", "png", "webp", "avif", "heic", "bmp", "gif", "tiff", "ico",
)

#: 支持读入的格式
INPUT_FORMATS: tuple[str, ...] = tuple(sorted(set(_EXT_TO_FMT) - {"jpe", "heif", "tif"}))

#: 带透明通道的保存格式
_ALPHA_FORMATS = {"PNG", "WEBP", "AVIF", "HEIF", "GIF", "TIFF", "ICO"}

#: 动画保存格式
_ANIMATED_FORMATS = {"GIF", "WEBP", "AVIF", "HEIF"}

_FONT_CANDIDATES: tuple[tuple[str, int | None], ...] = (
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\msyhbd.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", None),
    (r"C:\Windows\Fonts\simsun.ttc", 0),
    (r"C:\Windows\Fonts\Deng.ttf", None),
    (r"C:\Windows\Fonts\arial.ttf", None),
)

_POSITIONS = {
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
}


_heif_registered = False


def _ensure_heif() -> None:
    """按需注册 HEIF / AVIF 解码器（幂等）。"""
    global _heif_registered
    if _heif_registered:
        return
    _heif_registered = True
    try:
        import pillow_heif
    except Exception:  # pragma: no cover - 可选依赖缺失
        return
    for opener in ("register_heif_opener", "register_avif_opener"):
        fn = getattr(pillow_heif, opener, None)
        if callable(fn):
            try:
                fn()
            except Exception:  # pragma: no cover
                pass


def normalize_ext(ext: str) -> str:
    """把 ``.JPG`` / ``jpeg`` 之类统一成 ``jpg``。"""
    e = str(ext).strip().lower().lstrip(".")
    if e == "jpeg":
        return "jpg"
    if e == "tif":
        return "tiff"
    if e == "heif":
        return "heic"
    return e


def format_for_ext(ext: str) -> str | None:
    return _EXT_TO_FMT.get(normalize_ext(ext))


def available_formats() -> dict:
    """返回前端可用的输入/输出格式清单。"""
    return {
        "input": list(INPUT_FORMATS),
        "output": list(OUTPUT_FORMATS),
    }


# --------------------------------------------------------------------- 读入
def open_image(src: Path | str, *, frame: int = 0, auto_orient: bool = True) -> Image.Image:
    """打开图片并（默认）按 EXIF 方向摆正。"""
    _ensure_heif()
    path = Path(src)
    if not path.is_file():
        raise runner.EngineError(f"图片不存在：{path.name}")
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise runner.EngineError(f"无法读取图片 {path.name}：{exc}") from exc

    if frame and getattr(img, "n_frames", 1) > 1:
        try:
            img.seek(min(frame, img.n_frames - 1))
        except Exception:
            pass
    if auto_orient:
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
    return img


def frame_count(src: Path | str) -> int:
    _ensure_heif()
    try:
        with Image.open(src) as img:
            return int(getattr(img, "n_frames", 1) or 1)
    except Exception:
        return 1


def _exif_summary(img: Image.Image) -> dict:
    out: dict = {}
    try:
        exif = img.getexif()
    except Exception:
        return out
    if not exif:
        return out
    names = {271: "make", 272: "model", 274: "orientation", 305: "software",
             306: "datetime", 34855: "iso", 33437: "f_number", 33434: "exposure"}
    for tag, label in names.items():
        value = exif.get(tag)
        if value is not None:
            out[label] = str(value)
    return out


def info(src: Path | str) -> dict:
    """返回尺寸、模式、格式、帧数、DPI、EXIF 摘要等。"""
    path = Path(src)
    img = open_image(path, auto_orient=False)
    try:
        dpi = img.info.get("dpi")
        out = {
            "file": path.name,
            "size": path.stat().st_size,
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "format": img.format,
            "has_alpha": img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info,
            "frames": int(getattr(img, "n_frames", 1) or 1),
            "animated": int(getattr(img, "n_frames", 1) or 1) > 1,
            "megapixels": round(img.width * img.height / 1e6, 2),
        }
        if dpi:
            out["dpi"] = [round(float(dpi[0]), 2), round(float(dpi[1]), 2)]
        exif = _exif_summary(img)
        if exif:
            out["exif"] = exif
        out["aspect"] = round(img.width / img.height, 4) if img.height else None
        return out
    finally:
        img.close()


# --------------------------------------------------------------------- 保存
def parse_color(value, default=(255, 255, 255)) -> tuple[int, int, int]:
    """接受 ``"#rrggbb"``、``"255,255,255"``、``[r,g,b]``、``"red"`` 等。"""
    if value is None:
        return default
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return tuple(int(max(0, min(255, c))) for c in value[:3])  # type: ignore[return-value]
    text = str(value).strip()
    if not text:
        return default
    if text.startswith("#"):
        hexs = text[1:]
        if len(hexs) == 3:
            hexs = "".join(c * 2 for c in hexs)
        try:
            return (int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16))
        except ValueError:
            return default
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return default
    named = {
        "white": (255, 255, 255), "black": (0, 0, 0), "red": (220, 38, 38),
        "gray": (128, 128, 128), "grey": (128, 128, 128), "blue": (37, 99, 235),
    }
    return named.get(text.lower(), default)


def _flatten(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """把带透明通道的图压到纯色背景上（给 JPEG/BMP 用）。"""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode not in ("RGB", "L", "CMYK"):
        return img.convert("RGB")
    return img


def _animated_frames(img: Image.Image, fmt: str) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for i in range(int(getattr(img, "n_frames", 1) or 1)):
        try:
            img.seek(i)
        except EOFError:
            break
        frame = img.convert("RGBA") if fmt in _ALPHA_FORMATS else _flatten(img)
        frames.append(frame.copy())
    img.seek(0)
    return frames or [_flatten(img)]


def _save(
    img: Image.Image,
    dst: Path,
    fmt: str,
    *,
    quality: int = 90,
    background=(255, 255, 255),
    dpi: tuple[float, float] | None = None,
    all_frames: bool = True,
) -> Path:
    """把 PIL 图像写到 ``dst``，按格式选择合理参数。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    quality = int(max(1, min(100, quality)))
    fmt = fmt.upper()
    kwargs: dict = {}

    n_frames = int(getattr(img, "n_frames", 1) or 1)
    animated = n_frames > 1 and fmt in _ANIMATED_FORMATS and all_frames
    if animated:
        frames = _animated_frames(img, fmt)
        kwargs["save_all"] = True
        kwargs["append_images"] = frames[1:]
        duration = img.info.get("duration")
        if duration:
            kwargs["duration"] = duration
        kwargs["loop"] = img.info.get("loop", 0)
        img = frames[0]
    elif fmt not in _ALPHA_FORMATS:
        img = _flatten(img, background)

    if dpi:
        kwargs["dpi"] = dpi

    def _tune(target: str) -> dict:
        """按具体编码器补充参数。"""
        opts = dict(kwargs)
        if target == "JPEG":
            opts.update(quality=quality, optimize=True, progressive=True,
                        subsampling=0 if quality >= 90 else 2)
        elif target == "PNG":
            opts.update(optimize=True, compress_level=9)
        elif target == "WEBP":
            opts.update(quality=quality, method=6)
        elif target == "AVIF":
            opts.update(quality=quality, speed=6)
        elif target == "HEIF":
            opts.update(quality=quality)
        elif target == "GIF":
            if img.mode not in ("P", "L"):
                opts["_img"] = img.convert("RGBA").convert("P", palette=Image.ADAPTIVE)
        elif target == "TIFF":
            opts.update(compression="tiff_lzw")
        elif target == "ICO":
            opts.update(sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        elif target == "JPEG2000":
            opts.update(quality_mode="rates", quality_layers=[quality / 10.0])
        return opts

    candidates = [fmt]
    if fmt == "AVIF":
        candidates = ["AVIF", "HEIF"]      # 原生 Pillow 不支持时退回 pillow-heif
    elif fmt == "HEIF":
        candidates = ["HEIF", "AVIF"]

    last_error: Exception | None = None
    for cand in candidates:
        try:
            opts = _tune(cand)
            target_img = opts.pop("_img", img)
            target_img.save(dst, format=cand, **opts)
            if dst.is_file() and dst.stat().st_size > 0:
                if target_img is not img:
                    target_img.close()
                return dst
        except Exception as exc:  # 换下一个编码器
            last_error = exc
    raise runner.EngineError(
        f"保存 {dst.name} 失败：{last_error or '未生成文件'}（目标格式 {fmt}）"
    )


def save(
    img: Image.Image,
    dst: Path | str,
    *,
    quality: int = 90,
    background=(255, 255, 255),
    dpi: tuple[float, float] | None = None,
) -> Path:
    """按扩展名保存一张 PIL 图像。"""
    dst_path = Path(dst)
    fmt = format_for_ext(dst_path.suffix)
    if not fmt:
        raise runner.EngineError(f"不支持的输出图片格式：{dst_path.suffix or '(无扩展名)'}")
    return _save(img, dst_path, fmt, quality=quality, background=background, dpi=dpi)


def convert(
    src: Path | str,
    dst: Path | str,
    *,
    quality: int = 90,
    background=(255, 255, 255),
    auto_orient: bool = True,
    frame: int = 0,
    strip_metadata: bool = False,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """图片格式转换（保持原始像素尺寸）。"""
    if progress:
        progress(0.1)
    img = open_image(src, frame=frame, auto_orient=auto_orient)
    try:
        if progress:
            progress(0.5)
        dpi = None
        if strip_metadata:
            clean = Image.new(img.mode, img.size)
            clean.paste(img)
            img.close()
            img = clean
        else:
            dpi = img.info.get("dpi")
        return save(img, dst, quality=quality, background=background, dpi=dpi)
    finally:
        img.close()
        if progress:
            progress(1.0)


# --------------------------------------------------------------------- 缩放
def resize(
    src: Path | str,
    dst: Path | str,
    *,
    width: int | None = None,
    height: int | None = None,
    mode: str = "fit",
    background=(255, 255, 255),
    quality: int = 92,
    auto_orient: bool = True,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """缩放 / 补白 / 裁剪到目标尺寸。

    mode:
        ``fit``     保持比例缩到框内，输出为缩放后的真实尺寸（不补白）
        ``contain`` 保持比例，输出固定为框尺寸，多余部分用 ``background`` 补白
        ``cover``   保持比例填满框，溢出部分居中裁掉
        ``stretch`` 直接拉伸到框尺寸

    别名：``fill`` = ``cover``，``exact``/``stretch`` = ``stretch``。
    """
    mode = {"fill": "cover", "exact": "stretch", "stretch": "stretch",
            "contain": "contain", "fit": "fit", "cover": "cover"}.get(
        str(mode).lower(), str(mode).lower())
    img = open_image(src, auto_orient=auto_orient)
    try:
        if not width and not height:
            raise runner.EngineError("缩放需要至少指定宽度或高度。")
        if mode in ("fit", "contain") and not (width and height):
            mode = "fit"
        w0, h0 = img.size
        tw = int(width or 0)
        th = int(height or 0)

        if mode == "fit":
            if not tw:
                tw = max(1, round(w0 * th / h0))
            if not th:
                th = max(1, round(h0 * tw / w0))
            scale = min(tw / w0, th / h0)
            new_size = (max(1, round(w0 * scale)), max(1, round(h0 * scale)))
            out = img.resize(new_size, Image.LANCZOS)
        elif mode == "contain":
            tw = tw or w0
            th = th or h0
            scale = min(tw / w0, th / h0)
            new_size = (max(1, round(w0 * scale)), max(1, round(h0 * scale)))
            resized = img.resize(new_size, Image.LANCZOS)
            out = Image.new("RGBA", (tw, th), tuple(background) + (255,))
            out.paste(resized.convert("RGBA"), ((tw - new_size[0]) // 2, (th - new_size[1]) // 2))
            out = out.convert("RGB") if img.mode not in ("RGBA", "LA") else out
        elif mode == "cover":
            tw = tw or w0
            th = th or h0
            scale = max(tw / w0, th / h0)
            new_size = (max(1, math.ceil(w0 * scale)), max(1, math.ceil(h0 * scale)))
            resized = img.resize(new_size, Image.LANCZOS)
            left = (new_size[0] - tw) // 2
            top = (new_size[1] - th) // 2
            out = resized.crop((left, top, left + tw, top + th))
        elif mode == "stretch":
            out = img.resize((tw or w0, th or h0), Image.LANCZOS)
        else:
            raise runner.EngineError(f"未知缩放模式：{mode}")

        if progress:
            progress(0.8)
        return save(out, dst, quality=quality, background=background)
    finally:
        img.close()
        if progress:
            progress(1.0)


def compress(
    src: Path | str,
    dst: Path | str,
    *,
    quality: int = 75,
    max_dim: int | None = None,
    auto_orient: bool = True,
    progress: Callable[[float], None] | None = None,
) -> dict:
    """按 JPEG/WebP 思路压缩图片，返回前后体积。"""
    img = open_image(src, auto_orient=auto_orient)
    try:
        if max_dim and max(img.size) > max_dim:
            scale = max_dim / float(max(img.size))
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                             Image.LANCZOS)
        before = Path(src).stat().st_size
        save(img, dst, quality=quality)
        after = Path(dst).stat().st_size
        return {
            "before": before,
            "after": after,
            "saved_ratio": round(1 - after / before, 4) if before else 0.0,
            "width": img.width,
            "height": img.height,
            "quality": quality,
        }
    finally:
        img.close()
        if progress:
            progress(1.0)


# ------------------------------------------------------------- 裁剪 / 旋转
def crop(
    src: Path | str,
    dst: Path | str,
    *,
    left: int = 0,
    top: int = 0,
    right: int | None = None,
    bottom: int | None = None,
    unit: str = "px",
    quality: int = 92,
    auto_orient: bool = True,
) -> Path:
    """裁剪。``unit="%"`` 时四个值按百分比解释（0–100）。"""
    img = open_image(src, auto_orient=auto_orient)
    try:
        w, h = img.size
        if unit in ("%", "percent"):
            l = round(w * float(left) / 100)
            t = round(h * float(top) / 100)
            r = w - round(w * float(right or 0) / 100)
            b = h - round(h * float(bottom or 0) / 100)
        else:
            l = int(left)
            t = int(top)
            r = w - int(right or 0)
            b = h - int(bottom or 0)
        l, t = max(0, min(l, w - 1)), max(0, min(t, h - 1))
        r, b = max(l + 1, min(r, w)), max(t + 1, min(b, h))
        out = img.crop((l, t, r, b))
        if out.width < 1 or out.height < 1:
            raise runner.EngineError("裁剪区域为空，请检查参数。")
        return save(out, dst, quality=quality)
    finally:
        img.close()


def rotate(
    src: Path | str,
    dst: Path | str,
    *,
    angle: float,
    expand: bool = True,
    background=(255, 255, 255),
    quality: int = 92,
    auto_orient: bool = True,
) -> Path:
    """任意角度旋转（90 的整数倍为无损变换，其余角度会重采样）。"""
    img = open_image(src, auto_orient=auto_orient)
    try:
        norm = float(angle) % 360
        if norm in (0.0, 90.0, 180.0, 270.0):
            out = img.rotate(-norm, expand=expand, resample=Image.BICUBIC)
        else:
            out = img.rotate(-norm, expand=expand, resample=Image.BICUBIC,
                             fillcolor=tuple(background) + ((255,) if img.mode == "RGBA" else ()))
        return save(out, dst, quality=quality, background=background)
    finally:
        img.close()


def flip(
    src: Path | str,
    dst: Path | str,
    *,
    axis: str = "horizontal",
    quality: int = 92,
    auto_orient: bool = True,
) -> Path:
    """水平 / 垂直翻转。"""
    img = open_image(src, auto_orient=auto_orient)
    try:
        if axis in ("horizontal", "h", "x"):
            out = ImageOps.mirror(img)
        elif axis in ("vertical", "v", "y"):
            out = ImageOps.flip(img)
        else:
            raise runner.EngineError(f"未知翻转方向：{axis}（可选 horizontal / vertical）")
        return save(out, dst, quality=quality)
    finally:
        img.close()


# --------------------------------------------------------------------- 字体
def load_font(size: int) -> ImageFont.ImageFont:
    for path, index in _FONT_CANDIDATES:
        try:
            if Path(path).is_file():
                if index is None:
                    return ImageFont.truetype(path, size)
                return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    return ImageFont.load_default()


def _anchor_xy(position: str, canvas: tuple[int, int], box: tuple[int, int], margin: int):
    cw, ch = canvas
    bw, bh = box
    pos = position if position in _POSITIONS else "bottom-right"
    if pos.endswith("left"):
        x = margin
    elif pos.endswith("right"):
        x = cw - margin - bw
    else:
        x = (cw - bw) // 2
    if pos.startswith("top"):
        y = margin
    elif pos.startswith("bottom"):
        y = ch - margin - bh
    else:
        y = (ch - bh) // 2
    return x, y


# --------------------------------------------------------------------- 水印
def watermark_text(
    src: Path | str,
    dst: Path | str,
    *,
    text: str,
    position: str = "bottom-right",
    font_size: int = 0,
    color="#ffffff",
    opacity: float = 0.85,
    angle: float = 0.0,
    margin: int = 24,
    stroke: bool = True,
    quality: int = 92,
    auto_orient: bool = True,
) -> Path:
    """给图片叠加文字水印（默认右下角，带描边以便在任意底色上可读）。"""
    text = str(text or "").strip()
    if not text:
        raise runner.EngineError("水印文字不能为空。")
    img = open_image(src, auto_orient=auto_orient).convert("RGBA")
    try:
        size = int(font_size) if font_size else max(14, round(min(img.size) * 0.045))
        font = load_font(size)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2 if stroke else 0)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

        layer = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        rgb = parse_color(color, (255, 255, 255))
        alpha = int(max(0.0, min(1.0, float(opacity))) * 255)
        ld.text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=rgb + (alpha,),
                stroke_width=2 if stroke else 0, stroke_fill=(0, 0, 0, min(alpha, 160)))
        if angle:
            layer = layer.rotate(float(angle), expand=True, resample=Image.BICUBIC)
        x, y = _anchor_xy(position, img.size, layer.size, int(margin))
        img.alpha_composite(layer, (x, y))
        return save(img, dst, quality=quality)
    finally:
        img.close()


def watermark_image(
    src: Path | str,
    dst: Path | str,
    watermark: Path | str,
    *,
    position: str = "bottom-right",
    scale: float = 0.2,
    opacity: float = 0.6,
    margin: int = 24,
    quality: int = 92,
    auto_orient: bool = True,
) -> Path:
    """给图片叠加图片水印（保留 PNG 透明通道）。"""
    img = open_image(src, auto_orient=auto_orient).convert("RGBA")
    try:
        mark = open_image(watermark, auto_orient=False).convert("RGBA")
        target_w = max(1, round(img.width * float(scale)))
        target_h = max(1, round(mark.height * target_w / mark.width))
        mark = mark.resize((target_w, target_h), Image.LANCZOS)
        if opacity < 1.0:
            alpha = mark.split()[-1].point(lambda v: int(v * float(opacity)))
            mark.putalpha(alpha)
        x, y = _anchor_xy(position, img.size, mark.size, int(margin))
        img.alpha_composite(mark, (x, y))
        return save(img, dst, quality=quality)
    finally:
        img.close()


# --------------------------------------------------------------------- 拼接
def combine(
    images: Sequence[Path | str],
    dst: Path | str,
    *,
    direction: str = "vertical",
    gap: int = 0,
    background=(255, 255, 255),
    quality: int = 92,
) -> Path:
    """把多张图片按同方向拼接成一张。"""
    files = [Path(p) for p in images]
    if len(files) < 2:
        raise runner.EngineError("拼接至少需要两张图片。")
    imgs = [open_image(f).convert("RGBA") for f in files]
    try:
        bg = tuple(background) + (255,)
        if direction in ("vertical", "v", "column"):
            width = max(i.width for i in imgs)
            height = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
        elif direction in ("horizontal", "h", "row"):
            width = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
            height = max(i.height for i in imgs)
        else:
            raise runner.EngineError(f"未知拼接方向：{direction}")
        canvas = Image.new("RGBA", (width, height), bg)
        offset = 0
        for im in imgs:
            if direction in ("vertical", "v", "column"):
                canvas.alpha_composite(im, ((width - im.width) // 2, offset))
                offset += im.height + gap
            else:
                canvas.alpha_composite(im, (offset, (height - im.height) // 2))
                offset += im.width + gap
        return save(canvas, dst, quality=quality, background=background)
    finally:
        for im in imgs:
            im.close()


# --------------------------------------------------------------------- 其它
def strip(src: Path | str, dst: Path | str, *, auto_orient: bool = True) -> Path:
    """清除 EXIF / ICC 等元数据（彻底重写像素）。"""
    img = open_image(src, auto_orient=auto_orient)
    try:
        clean = Image.new(img.mode, img.size)
        clean.paste(img)
        return save(clean, dst)
    finally:
        img.close()


def to_ico(src: Path | str, dst: Path | str, *, size: int = 256) -> Path:
    img = open_image(src)
    try:
        img = img.convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        return _save(img, Path(dst), "ICO")
    finally:
        img.close()


def preview_bytes(src: Path | str, *, max_dim: int = 1600, quality: int = 82) -> tuple[bytes, str]:
    """生成缩略预览，返回 ``(bytes, mime)``。动图取第一帧。"""
    img = open_image(src, auto_orient=True)
    try:
        if max(img.size) > max_dim:
            scale = max_dim / float(max(img.size))
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.convert("RGBA").save(buf, format="PNG", optimize=True)
            return buf.getvalue(), "image/png"
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    finally:
        img.close()


def batch(
    inputs: Iterable[Path | str],
    outdir: Path | str,
    op: str,
    *,
    params: dict | None = None,
    progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """对一批图片依次执行同一个操作（供任务系统使用）。"""
    files = [Path(p) for p in inputs]
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    params = dict(params or {})
    out_ext = normalize_ext(params.pop("format", "") or "")
    fn = _BATCH_OPS.get(op)
    if fn is None:
        raise runner.EngineError(f"不支持的图片操作：{op}")

    produced: list[Path] = []
    for i, src in enumerate(files):
        # 产物扩展名默认跟随源文件，但 to_ico 之类必须固定成自己的格式，
        # 否则会写出「.png 里面装 ICO 字节」的错误文件。
        dst_ext = out_ext or _BATCH_OUT_EXT.get(op) or normalize_ext(src.suffix) or "png"
        if op == "convert" and not out_ext:
            raise runner.EngineError("图片格式转换需要指定 format 参数。")
        dst = _unique_path(out_dir, f"{src.stem}_out.{dst_ext}")
        fn(src, dst, **_accepted_kwargs(fn, params))
        produced.append(dst)
        if progress:
            progress((i + 1) / len(files))
    return produced


def _accepted_kwargs(fn: Callable[..., Path], params: dict) -> dict:
    """只把目标函数真正接受的参数传进去。

    任务系统传下来的是 TaskParams 的全量字段（含别的操作才用的 mode/width 等），
    直接 ``**params`` 会 TypeError，所以按函数签名过滤一次。
    """
    sig = inspect.signature(fn)
    allowed = {
        name
        for name, p in sig.parameters.items()
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and name not in ("src", "dst")
    }
    return {k: v for k, v in params.items() if k in allowed}


def _unique_path(directory: Path, name: str) -> Path:
    """同名文件自动加序号，避免批量处理时互相覆盖。"""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"


_BATCH_OPS: dict[str, Callable[..., Path]] = {
    "convert": convert,
    "resize": resize,
    "compress": compress,
    "crop": crop,
    "rotate": rotate,
    "flip": flip,
    "watermark_text": watermark_text,
    "watermark_image": watermark_image,
    "strip": strip,
    "to_ico": to_ico,
}

#: 产物扩展名与源文件无关的操作
_BATCH_OUT_EXT: dict[str, str] = {"to_ico": "ico"}


__all__ = [
    "INPUT_FORMATS", "OUTPUT_FORMATS", "available_formats", "format_for_ext",
    "normalize_ext", "open_image", "info", "frame_count", "save", "convert",
    "resize", "compress", "crop", "rotate", "flip", "watermark_text",
    "watermark_image", "combine", "strip", "to_ico", "preview_bytes", "batch",
    "parse_color", "load_font",
]
