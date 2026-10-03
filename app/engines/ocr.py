"""OCR 引擎：ocrmypdf（MPL-2.0）+ Tesseract（Apache-2.0）。

设计要点
--------
* 纯 Python 的 rapidocr-onnxruntime 需要 Python < 3.13，本机是 3.13，
  因此 OCR 统一走 ``ocrmypdf + Tesseract`` 这条链路。
* Tesseract 不在系统 PATH 里也能用：调用前把它所在目录塞进子进程 PATH，
  并把 ``TESSDATA_PREFIX`` 指向项目内的 tessdata。
* ocrmypdf 16+ 的 PDF/A 转换与压缩全部由 pikepdf 完成，**不需要
  Ghostscript**；这也正是本项目能保持 MIT 许可的原因之一。
* 对图片输入，直接用 ``tesseract <img> <out> pdf`` 生成「原图 + 隐形文字层」，
  多张图片各生成一页后交给 :mod:`app.engines.pdfops` 合并，避免二次编码。

安装：``pwsh tools\\fetch_tesseract.ps1``（Tesseract 二进制 + chi_sim 语言包）
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .. import config
from . import pdfops, registry, runner

#: 输出类型 → ocrmypdf 的 --output-type 取值
OUTPUT_TYPES = {
    "pdf": "pdf",
    "pdfa": "pdfa",
    "pdfa-1": "pdfa-1",
    "pdfa-2": "pdfa-2",
    "pdfa-3": "pdfa-3",
}

DEFAULT_LANGS = ("chi_sim", "eng")

#: ocrmypdf 会用到、但没装也不影响主流程的可选外部工具
_OPTIONAL_TOOLS = ("pngquant", "jbig2", "unpaper")


def tesseract_exe() -> Path | None:
    return registry.resolve("tesseract")


def available() -> bool:
    """Tesseract 与 ocrmypdf 都就绪才算可用。"""
    if tesseract_exe() is None:
        return False
    try:
        import ocrmypdf  # noqa: F401
    except Exception:
        return False
    return True


def status() -> dict:
    exe = tesseract_exe()
    tessdata = registry.tessdata_dir()
    try:
        import ocrmypdf as _ocrmypdf
        ocrmypdf_version = getattr(_ocrmypdf, "__version__", "unknown")
    except Exception:
        ocrmypdf_version = None

    return {
        "available": available(),
        "tesseract": str(exe) if exe else None,
        "tesseract_version": version(),
        "tessdata": str(tessdata) if tessdata else None,
        "ocrmypdf": ocrmypdf_version,
        "languages": languages(),
        "installed": installed_languages(),
        "optional_tools": {name: shutil.which(name) is not None for name in _OPTIONAL_TOOLS},
        "hint": None if available() else "运行 pwsh tools\\fetch_tesseract.ps1 安装 Tesseract 与 chi_sim 语言包",
    }


def version() -> str | None:
    exe = tesseract_exe()
    if not exe:
        return None
    try:
        out = runner.run([str(exe), "--version"], timeout=30, check=False)
        first = (out.stdout or out.stderr).strip().splitlines()
        return first[0].strip() if first else None
    except runner.EngineError:
        return None


def installed_languages() -> list[str]:
    return registry.installed_langs()


def languages() -> dict[str, str]:
    """返回全部受支持语言的 ``{代码: 中文名}``，是否已安装见 ``installed_languages()``。

    刻意只返回扁平的 name 映射：前端把这张表直接当 ``{code: label}`` 用，
    嵌套结构会让标签渲染成 [object Object]。
    """
    return dict(config.OCR_LANGS)


def resolve_languages(langs: Sequence[str] | None) -> list[str]:
    """校验并规范化语言代码列表。"""
    if not langs:
        return list(DEFAULT_LANGS)
    requested = [str(x).strip() for x in langs if str(x).strip()]
    unknown = [x for x in requested if x not in config.OCR_LANGS]
    if unknown:
        raise runner.EngineError(f"不支持的 OCR 语言：{', '.join(unknown)}")
    installed = set(installed_languages())
    missing = [x for x in requested if installed and x not in installed]
    if missing:
        raise runner.EngineError(
            f"语言包未安装：{', '.join(missing)}。"
            "请运行 pwsh tools\\fetch_tesseract.ps1 补齐。"
        )
    return requested


def _env(langs: Sequence[str], jobs: int | None = None) -> dict[str, str]:
    """构造带 Tesseract 路径与 tessdata 的子进程环境。"""
    exe = tesseract_exe()
    extra: dict[str, str] = {}
    if exe:
        extra["PATH"] = str(exe.parent) + os.pathsep + os.environ.get("PATH", "")
    tessdata = registry.tessdata_dir()
    if tessdata:
        extra["TESSDATA_PREFIX"] = str(tessdata)
    if jobs:
        extra["OMP_THREAD_LIMIT"] = str(max(1, int(jobs)))
    return extra


def ocrmypdf_cmd() -> list[str]:
    """优先用当前解释器执行 ocrmypdf 模块，避免 PATH 里找不到入口脚本。"""
    return [sys.executable, "-m", "ocrmypdf"]


def _has_text_layer(src: Path | str, pages: int = 3, min_chars: int = 24) -> bool:
    """粗判 PDF 是否已有文字层（决定用 --skip-text 还是强制 OCR）。"""
    try:
        chunks = pdfops.extract_text(src)
    except Exception:
        return False
    text = "".join(item.get("text", "") for item in chunks[:pages])
    return len(text.strip()) >= min_chars


def ocr_pdf(
    src: Path | str,
    dst: Path | str,
    *,
    languages: Sequence[str] | None = None,
    output_type: str = "pdf",
    force: bool = False,
    skip_text: bool = False,
    redo_ocr: bool = False,
    deskew: bool = False,
    rotate_pages: bool = False,
    remove_background: bool = False,
    clean: bool = False,
    optimize: int = 1,
    sidecar: Path | str | None = None,
    jobs: int | None = None,
    timeout: int | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict:
    """给 PDF 加可搜索文字层。

    ``force`` / ``skip_text`` / ``redo_ocr`` 三者互斥；都不传时自动判断：
    已有文字层就走 ``--skip-text``，否则正常 OCR。
    """
    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.is_file():
        raise runner.EngineError(f"文件不存在：{src_path.name}")
    if not available():
        raise runner.EngineError(
            "OCR 引擎不可用：缺少 Tesseract 或 ocrmypdf。"
            "请运行 pwsh tools\\fetch_tesseract.ps1"
        )

    langs = resolve_languages(list(languages) if languages else None)
    out_type = OUTPUT_TYPES.get(str(output_type).lower())
    if out_type is None:
        raise runner.EngineError(f"不支持的输出类型：{output_type}；可选 {', '.join(OUTPUT_TYPES)}")

    forced = [name for name, flag in (("force", force), ("skip_text", skip_text), ("redo_ocr", redo_ocr)) if flag]
    if len(forced) > 1:
        raise runner.EngineError(f"参数冲突：{' / '.join(forced)} 只能选一个。")

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()

    cmd = ocrmypdf_cmd()
    cmd += ["-l", "+".join(langs)]
    if force:
        cmd.append("--force-ocr")
    elif redo_ocr:
        cmd.append("--redo-ocr")
    elif skip_text or _has_text_layer(src_path):
        cmd.append("--skip-text")
    if deskew:
        cmd.append("--deskew")
    if rotate_pages:
        cmd.append("--rotate-pages")
    if remove_background:
        cmd.append("--remove-background")
    if clean:
        cmd.append("--clean")
    if out_type != "pdf":
        cmd += ["--output-type", out_type]
    if optimize:
        cmd += ["--optimize", str(int(optimize))]
    n_jobs = int(jobs or min(4, os.cpu_count() or 1))
    if n_jobs > 1:
        cmd += ["--jobs", str(n_jobs)]
    if sidecar:
        Path(sidecar).parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--sidecar", str(sidecar)]
    cmd += [str(src_path), str(dst_path)]

    if progress:
        progress(0.05)
    runner.run(
        cmd,
        timeout=timeout or max(600, 240 * max(1, pdfops.page_count(src_path)) // 10),
        env_extra=_env(langs, n_jobs),
    )
    if progress:
        progress(0.95)

    if not dst_path.is_file() or dst_path.stat().st_size == 0:
        raise runner.EngineError("OCR 未生成输出文件。")

    result = {
        "before": src_path.stat().st_size,
        "after": dst_path.stat().st_size,
        "pages": pdfops.page_count(dst_path),
        "languages": langs,
        "output_type": out_type,
        "deskew": deskew,
        "rotate_pages": rotate_pages,
        "source": src_path.name,
    }
    if progress:
        progress(1.0)
    return result


def ocr_image_to_pdf(
    src: Path | str,
    dst: Path | str,
    *,
    languages: Sequence[str] | None = None,
    dpi: int = 300,
    timeout: int = 600,
) -> Path:
    """单张图片 → 带隐形文字层的 PDF（Tesseract 原生 pdf 输出配置）。"""
    exe = tesseract_exe()
    if exe is None:
        raise runner.EngineError("Tesseract 未安装，无法对图片做 OCR。请运行 pwsh tools\\fetch_tesseract.ps1")
    langs = resolve_languages(list(languages) if languages else None)

    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    base = dst_path.with_suffix("")
    runner.run(
        [str(exe), str(src_path), str(base), "-l", "+".join(langs), "--dpi", str(int(dpi)), "pdf"],
        timeout=timeout,
        env_extra=_env(langs),
    )
    produced = base.with_suffix(".pdf")
    if not produced.is_file():
        raise runner.EngineError(f"Tesseract 未生成 PDF：{produced.name}")
    if produced != dst_path:
        if dst_path.exists():
            dst_path.unlink()
        produced.replace(dst_path)
    return dst_path


def image_text(
    src: Path | str,
    *,
    languages: Sequence[str] | None = None,
    timeout: int = 300,
) -> str:
    """对图片直接做 OCR，返回纯文本。"""
    exe = tesseract_exe()
    if exe is None:
        raise runner.EngineError("Tesseract 未安装，无法识别图片文字。")
    langs = resolve_languages(list(languages) if languages else None)
    result = runner.run(
        [str(exe), str(src), "stdout", "-l", "+".join(langs), "--dpi", "300"],
        timeout=timeout,
        env_extra=_env(langs),
    )
    return result.stdout


def to_text(
    src: Path | str,
    dst: Path | str,
    *,
    languages: Sequence[str] | None = None,
    timeout: int = 900,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """任何输入 → 纯文本：图片走 Tesseract，PDF 先 OCR 再抽文字。"""
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    langs = resolve_languages(list(languages) if languages else None)

    if src_path.suffix.lower() in config.IMAGE_EXTS:
        text = image_text(src_path, languages=langs, timeout=timeout)
    else:
        tmp = config.temp_dir()
        try:
            ocr_out = tmp / f"{src_path.stem}_ocr.pdf"
            if progress:
                progress(0.1)
            ocr_pdf(src_path, ocr_out, languages=langs, skip_text=True, progress=progress)
            chunks = pdfops.extract_text(ocr_out)
            text = "\n\n".join(f"--- 第 {item['page']} 页 ---\n{item['text']}" for item in chunks)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    dst_path.write_text(text, encoding="utf-8")
    if progress:
        progress(1.0)
    return dst_path


def run(
    inputs: Iterable[Path | str],
    outdir: Path | str,
    *,
    languages: Sequence[str] | None = None,
    output_type: str = "pdf",
    **flags,
) -> list[Path]:
    """批量 OCR：PDF 逐个处理；图片每张一页并合并为一个 PDF。

    任务系统（:mod:`app.jobs`）使用的统一入口。
    """
    files = [Path(p) for p in inputs]
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not files:
        raise runner.EngineError("OCR 至少需要一个输入文件。")
    langs = resolve_languages(list(languages) if languages else None)
    progress: Callable[[float], None] | None = flags.pop("progress", None)

    pdfs = [f for f in files if f.suffix.lower() in config.PDF_EXTS]
    images = [f for f in files if f.suffix.lower() in config.IMAGE_EXTS]
    produced: list[Path] = []
    total = len(files)
    done = 0

    for pdf in pdfs:
        dst = out_dir / f"{pdf.stem}_ocr.pdf"
        ocr_pdf(pdf, dst, languages=langs, output_type=output_type, **flags)
        produced.append(dst)
        done += 1
        if progress:
            progress(done / total)

    if images:
        if output_type == "txt":
            for img in images:
                dst = out_dir / f"{img.stem}_ocr.txt"
                dst.write_text(image_text(img, languages=langs), encoding="utf-8")
                produced.append(dst)
                done += 1
                if progress:
                    progress(done / total)
        else:
            tmp = config.temp_dir()
            try:
                pages: list[Path] = []
                for img in images:
                    page = tmp / f"{img.stem}_ocr.pdf"
                    ocr_image_to_pdf(img, page, languages=langs)
                    pages.append(page)
                if len(pages) == 1:
                    dst = out_dir / f"{images[0].stem}_ocr.pdf"
                    shutil.copyfile(pages[0], dst)
                else:
                    dst = out_dir / "images_ocr.pdf"
                    pdfops.merge(pages, dst)
                produced.append(dst)
                done += len(images)
                if progress:
                    progress(done / total)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    if not produced:
        raise runner.EngineError("没有可 OCR 的输入文件（支持 PDF 与常见图片格式）。")
    return produced


__all__ = [
    "OUTPUT_TYPES", "DEFAULT_LANGS", "available", "status", "version",
    "languages", "installed_languages", "resolve_languages", "tesseract_exe",
    "ocr_pdf", "ocr_image_to_pdf", "image_text", "to_text", "run",
]
