"""转换链路规划器。

docpix 的核心思想：不自己实现格式解析，而是把「源格式 → 目标格式」的问题
拆成一条由成熟引擎组成的**链路**，再逐跳执行。

典型例子
--------
``md → pdf``：Pandoc **不能**直接产出 PDF（它需要 LaTeX/wkhtmltopdf 等外部
排版引擎），因此链路规划为::

    pandoc (md → html)  →  LibreOffice (html → pdf)

``pdf → md``：LibreOffice 先把 PDF 用 Draw 导入成 docx，再交给 Pandoc 转 md。

其它组合要么直接命中单跳（``docx → pdf`` 走 LibreOffice），要么明确报错并给出
可读的原因，绝不静默产出错误结果。

本模块只做「规划 + 执行编排」，具体引擎实现在 :mod:`app.engines` 下。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import config
from .engines import imageops, office, pandoc, pdfops, runner

#: 扩展名（不含点）分类
IMAGE = {e.lstrip(".") for e in config.IMAGE_EXTS}
OFFICE = {e.lstrip(".") for e in office.INPUT_EXTS}
#: Pandoc 能「读」的源格式
PANDOC = {e.lstrip(".") for e in pandoc.EXT_TO_FORMAT}
#: Pandoc 能「写」的目标格式（比可读格式少：没有 csv/tsv/xlsx）
PANDOC_OUT = {d.lstrip(".") for d in pandoc.FORMAT_TO_EXT.values()}
PDF = {"pdf"}
#: Pandoc 能直接读、LibreOffice 读不好的标记语言
MARKUP = {
    "md", "markdown", "mdown", "rst", "org", "tex", "latex", "typ", "adoc",
    "textile", "mediawiki", "opml", "epub", "ipynb",
}
TEXT = {"txt", "csv", "tsv", "json", "html", "htm", "xhtml", "rtf"}

#: 兼容别名 → 规范扩展名
_ALIAS = {
    "jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic",
    "markdown": "md", "mdown": "md", "htm": "html", "xhtml": "html",
    "latex": "tex", "text": "txt", "gfm": "md",
}


class PlanError(runner.EngineError):
    """无法为该组合规划出链路。"""


def norm_ext(ext: str | Path) -> str:
    """把 ``.DOCX`` / ``jpeg`` / 完整路径统一成规范扩展名（不含点）。"""
    if isinstance(ext, Path):
        text = ext.suffix
    else:
        text = str(ext).strip()
        if "/" in text or "\\" in text:
            text = Path(text).suffix
        elif text.startswith("."):
            text = text
        elif "." in text:
            text = Path(text).suffix
    e = text.strip().lower().lstrip(".")
    return _ALIAS.get(e, e)


@dataclass(frozen=True)
class Step:
    """链路中的一跳。"""

    op: str          # copy | office | pandoc | image | image_to_pdf | pdf_to_image | pdf_text
    target: str      # 该跳之后的扩展名（不含点）
    label: str       # 中文说明（给前端展示）
    note: str = ""

    def as_dict(self) -> dict:
        return {"op": self.op, "target": self.target, "label": self.label, "note": self.note}


@dataclass
class Plan:
    src_ext: str
    dst_ext: str
    steps: list[Step] = field(default_factory=list)
    warning: str = ""

    @property
    def target(self) -> str:
        return self.dst_ext

    def as_dict(self) -> dict:
        return {
            "src": self.src_ext,
            "target": self.dst_ext,
            "steps": [s.as_dict() for s in self.steps],
            "warning": self.warning,
        }


# ------------------------------------------------------------------ 规划
def _pdf_to_text_plan(dst: str) -> list[Step]:
    return [Step("pdf_text", dst, "用 pypdf 抽取 PDF 文字层", "若 PDF 是扫描件，请改用 OCR 功能")]


def plan(src: str | Path, dst: str | Path) -> Plan:
    """规划从 ``src`` 到 ``dst`` 的转换链路。"""
    se, de = norm_ext(src), norm_ext(dst)
    if not se:
        raise PlanError("无法识别源文件扩展名。")
    if not de:
        raise PlanError("请指定目标格式（例如 pdf / docx / md / png）。")

    if se == de:
        return Plan(se, de, [Step("copy", de, f"已是 {de} 格式，直接复制")])

    # 图片 ⇄ 图片 / PDF
    if se in IMAGE:
        if de in IMAGE:
            return Plan(se, de, [Step("image", de, f"图片转换为 {de.upper()}")])
        if de == "pdf":
            return Plan(se, de, [Step("image_to_pdf", "pdf", "图片合成 PDF", "多张图片请使用「图片转 PDF」批量操作")])
        raise PlanError(f"暂不支持从图片（{se}）转换到 {de}；可先转成 PDF 再继续。")

    if se == "pdf":
        if de in IMAGE:
            return Plan(se, de, [Step("pdf_to_image", de, f"PDF 渲染为 {de.upper()} 图片", "按页输出多个文件")])
        if de in ("txt", "text"):
            return Plan(se, de, _pdf_to_text_plan("txt"))
        if de in OFFICE:
            return Plan(
                se, de,
                [Step("office", de, f"LibreOffice 以 Draw 导入 PDF 并导出 {de.upper()}")],
                warning="PDF 不是结构化文档，转换后需要人工校对版式。",
            )
        if de in PANDOC_OUT:
            steps = [Step("office", "docx", "LibreOffice 以 Draw 导入 PDF 并转 docx",
                          "PDF 的复杂版式可能发生位移")]
            steps.append(Step("pandoc", de, f"Pandoc 将 docx 转为 {de}"))
            return Plan(se, de, steps, warning="PDF 不是结构化文档，转换后需要人工校对版式。")
        raise PlanError(f"暂不支持 PDF → {de}。")

    # 目标为 PDF：这是最常见的需求
    if de == "pdf":
        if se in OFFICE and se not in MARKUP:
            return Plan(se, de, [Step("office", "pdf", "LibreOffice 直接导出 PDF")])
        if se in PANDOC or se in MARKUP:
            steps = [Step("pandoc", "html", f"Pandoc 将 {se} 转为 HTML"),
                     Step("office", "pdf", "LibreOffice 将 HTML 排版导出 PDF")]
            return Plan(se, de, steps,
                        warning="Pandoc 无法直接生成 PDF，这里走 pandoc → html → LibreOffice → pdf 链路。")
        if se in OFFICE:
            return Plan(se, de, [Step("office", "pdf", "LibreOffice 直接导出 PDF")])
        raise PlanError(f"暂不支持 {se} → pdf。")

    # 目标为文本/标记语言：优先 Pandoc 直转
    if de in PANDOC_OUT:
        if se in ("pdf",):
            raise PlanError(f"暂不支持 PDF → {de}。")
        if de in ("docx", "odt", "rtf", "epub", "pptx") and se in OFFICE and se not in PANDOC:
            return Plan(se, de, [Step("office", de, f"LibreOffice 转为 {de.upper()}")])
        if se in PANDOC or se in MARKUP or se in TEXT:
            return Plan(se, de, [Step("pandoc", de, f"Pandoc 将 {se} 转为 {de}")])
        if se in OFFICE:
            return Plan(se, de, [Step("office", "docx", "LibreOffice 先转 docx"),
                                 Step("pandoc", de, f"Pandoc 将 docx 转为 {de}")])
        raise PlanError(f"暂不支持 {se} → {de}；可先转成 docx 或 html 再继续。")

    # 目标为 Office 格式（Pandoc 写不了的目标，例如 xlsx/xls/csv 走 LibreOffice）
    if de in OFFICE:
        if se in OFFICE:
            return Plan(se, de, [Step("office", de, f"LibreOffice 转为 {de.upper()}")])
        if se in PANDOC or se in MARKUP or se in TEXT:
            return Plan(
                se, de,
                [Step("pandoc", "html", f"Pandoc 将 {se} 转为 HTML"),
                 Step("office", de, f"LibreOffice 将 HTML 转为 {de.upper()}")],
                warning=f"Pandoc 没有 {de} writer，改走 HTML 中转。",
            )
        raise PlanError(f"暂不支持 {se} → {de}。")

    raise PlanError(
        f"暂不支持 {se} → {de}。可用目标：图片格式、pdf、"
        f"{', '.join(sorted(set(OFFICE) | PANDOC_OUT)[:12])} …"
    )


def plan_for_files(src: str | Path, dst: str | Path) -> dict:
    """给前端用：返回链路 JSON。"""
    return plan(src, dst).as_dict()


def reachable_targets(src_ext: str) -> list[str]:
    """列出某个源格式可转换到的全部目标（用于前端下拉）。"""
    se = norm_ext(src_ext)
    candidates: list[str] = []
    base = ["pdf", "txt", "html", "docx", "odt", "rtf", "epub", "md", "rst", "org", "tex",
            "pptx", "xlsx", "csv", "tsv", "json", "opml", "typ", "adoc",
            "jpg", "png", "webp", "avif", "heic", "bmp", "gif", "tiff", "ico"]
    for de in base:
        if de == se:
            continue
        try:
            plan(se, de)
        except PlanError:
            continue
        candidates.append(de)
    return candidates


def matrix() -> dict[str, list[str]]:
    """源格式 → 可达目标格式的映射，供前端「转换」页使用。"""
    sources = sorted((IMAGE | OFFICE | PANDOC | set(PDF) | TEXT | {"md"}) - {"jpe", "tif", "htm", "xhtml"})
    return {src: reachable_targets(src) for src in sources}


# ------------------------------------------------------------------ 执行
def _step_progress(index: int, total: int) -> tuple[float, float]:
    span = 1.0 / max(1, total)
    return index * span, (index + 1) * span


def execute(
    inputs: Sequence[Path | str],
    outdir: Path | str,
    target: str,
    *,
    options: dict | None = None,
    progress: Callable[[float], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[Path]:
    """按规划好的链路执行转换。

    Args:
        inputs: 输入文件（多个文件会各自独立走一遍链路）。
        outdir: 输出目录。
        target: 目标扩展名（不含点）。
        options: 额外参数，如 ``format`` / ``quality`` / ``dpi`` / ``pages``。
        progress: 进度回调，参数为 0–1 的浮点数。
        cancel_check: 返回 True 时中断。

    Returns:
        产出的文件路径列表（PDF → 图片可能一次产出多个）。
    """
    files = [Path(p) for p in inputs]
    if not files:
        raise PlanError("没有输入文件。")
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = dict(options or {})
    produced: list[Path] = []

    for idx, src in enumerate(files):
        if cancel_check and cancel_check():
            break
        item_progress = None
        if progress:
            def item_progress(value: float, idx=idx, n=len(files)) -> None:  # type: ignore[misc]
                progress((idx + value) / n)
        produced.extend(_execute_one(src, out_dir, target, opts, item_progress, cancel_check))

    if progress:
        progress(1.0)
    if not produced:
        raise PlanError("转换没有产生任何输出文件。")
    return produced


def _execute_one(
    src: Path,
    out_dir: Path,
    target: str,
    opts: dict,
    progress: Callable[[float], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> list[Path]:
    p = plan(src, target)
    work = config.temp_dir()
    try:
        current = src
        total = len(p.steps)
        for i, step in enumerate(p.steps):
            if cancel_check and cancel_check():
                return []
            lo, hi = _step_progress(i, total)

            def sub(value: float, lo=lo, hi=hi) -> None:
                if progress:
                    progress(lo + (hi - lo) * max(0.0, min(1.0, value)))

            last = i == total - 1
            if step.op == "copy":
                dst = out_dir / f"{src.stem}.{target}"
                shutil.copyfile(src, dst)
                current = dst
                if sub:
                    sub(1.0)
                continue

            if step.op == "image":
                dst = out_dir / f"{src.stem}.{step.target}"
                imageops.convert(current, dst, quality=int(opts.get("quality", 92)),
                                 background=imageops.parse_color(opts.get("background")),
                                 progress=sub)
                current = dst
                continue

            if step.op == "image_to_pdf":
                dst = out_dir / f"{src.stem}.pdf"
                pdfops.images_to_pdf([current], dst,
                                     page_size=opts.get("page_size", "fit"),
                                     margin_mm=float(opts.get("margin_mm", 0) or 0),
                                     quality=int(opts.get("quality", 92)))
                current = dst
                if sub:
                    sub(1.0)
                continue

            if step.op == "pdf_to_image":
                files = pdfops.to_images(current, out_dir,
                                         dpi=int(opts.get("dpi", 150) or 150),
                                         fmt=opts.get("format") or step.target,
                                         pages=opts.get("pages"),
                                         quality=int(opts.get("quality", 90)))
                if sub:
                    sub(1.0)
                return files

            if step.op == "pdf_text":
                dst = out_dir / f"{src.stem}.txt"
                chunks = pdfops.extract_text(current, pages=opts.get("pages"))
                text = "\n\n".join(f"--- 第 {c['page']} 页 ---\n{c['text']}" for c in chunks)
                dst.write_text(text, encoding="utf-8")
                current = dst
                if sub:
                    sub(1.0)
                continue

            if step.op == "pandoc":
                if last and step.target == target:
                    dst = out_dir / f"{src.stem}.{target}"
                else:
                    dst = work / f"{i:02d}.{step.target}"
                pandoc.convert(current, dst, to_format=pandoc.format_for_ext(step.target))
                current = dst
                if sub:
                    sub(1.0)
                continue

            if step.op == "office":
                if last and step.target == target:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    produced = office.convert(current, out_dir, step.target)
                    current = produced
                    if sub:
                        sub(1.0)
                    break
                dst_dir = work / f"lo{i:02d}"
                current = office.convert(current, dst_dir, step.target)
                if sub:
                    sub(1.0)
                continue

            raise PlanError(f"未知的链路步骤：{step.op}")

        # 最终产物统一改名为「源文件名.目标扩展名」：
        # 中间步骤用的是 00.html 这类临时名，LibreOffice 会按输入名输出 00.pdf。
        if current and current.is_file():
            desired = out_dir / f"{src.stem}.{target}"
            if current.parent == out_dir:
                if current != desired:
                    if desired.exists():
                        desired.unlink()
                    current = current.rename(desired)
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                if desired.exists():
                    desired.unlink()
                shutil.copyfile(current, desired)
                current = desired
        return [current]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def capability_summary() -> dict:
    """系统自检页展示用的能力矩阵（体积可控）。"""
    return {
        "matrix": matrix(),
        "office_targets": list(office.OUTPUT_TARGETS),
        "pandoc_targets": sorted(pandoc.FORMAT_TO_EXT.keys()),
        "image_output": list(imageops.OUTPUT_FORMATS),
        "pdf_ops": [
            "merge", "split", "extract_pages", "delete_pages", "reorder", "rotate",
            "compress", "encrypt", "decrypt", "page_numbers", "watermark_text",
            "watermark_image", "pdf_to_images", "images_to_pdf", "extract_text",
            "metadata", "linearize", "repair",
        ],
        "image_ops": [
            "convert", "resize", "compress", "crop", "rotate", "flip",
            "watermark_text", "watermark_image", "strip", "combine", "to_ico",
        ],
        "ocr_output_types": ["pdf", "pdfa", "pdfa-1", "pdfa-2", "pdfa-3", "txt"],
    }


__all__ = [
    "Step", "Plan", "PlanError", "plan", "plan_for_files", "execute",
    "matrix", "reachable_targets", "capability_summary", "norm_ext",
    "IMAGE", "OFFICE", "PANDOC", "PANDOC_OUT", "PDF", "MARKUP", "TEXT",
]
