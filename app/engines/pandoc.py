"""Pandoc 转换引擎（可选依赖）。

许可说明：Pandoc 本身是 GPL-2.0-or-later，本项目以**独立进程**方式调用它，
不与本项目代码链接，因此 docpix 的 MIT 许可不受影响。它也不随仓库分发，
由 ``tools/fetch_engines.ps1`` 从官网按需下载。

Pandoc 不能直接产出 PDF（需要 LaTeX/wkhtmltopdf 之类外部引擎），
``md → pdf`` 这类需求由 :mod:`app.convert` 规划成
``pandoc → html → LibreOffice → pdf`` 的链路。
"""

from __future__ import annotations

from pathlib import Path

from . import registry, runner

#: 扩展名 → Pandoc 读写器名称
EXT_TO_FORMAT: dict[str, str] = {
    ".md": "gfm",
    ".markdown": "gfm",
    ".mdown": "gfm",
    ".txt": "markdown",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".docx": "docx",
    ".odt": "odt",
    ".epub": "epub",
    ".tex": "latex",
    ".latex": "latex",
    ".rst": "rst",
    ".org": "org",
    ".rtf": "rtf",
    ".ipynb": "ipynb",
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".opml": "opml",
    ".typ": "typst",
    ".adoc": "asciidoc",
}

#: 目标格式 → 输出扩展名（仅收录 Pandoc 真正支持的 writer，见
#: ``pandoc --list-output-formats``；注意 Pandoc **不能**写 csv/tsv/xlsx，
#: 这些交给 LibreOffice）
FORMAT_TO_EXT: dict[str, str] = {
    "gfm": ".md",
    "markdown": ".md",
    "commonmark": ".md",
    "commonmark_x": ".md",
    "html": ".html",
    "html4": ".html",
    "html5": ".html",
    "chunkedhtml": ".html",
    "docx": ".docx",
    "odt": ".odt",
    "opendocument": ".odt",
    "epub": ".epub",
    "epub2": ".epub",
    "epub3": ".epub",
    "latex": ".tex",
    "rst": ".rst",
    "org": ".org",
    "rtf": ".rtf",
    "ipynb": ".ipynb",
    "plain": ".txt",
    "txt": ".txt",
    "json": ".json",
    "pptx": ".pptx",
    "opml": ".opml",
    "typst": ".typ",
    "asciidoc": ".adoc",
    "mediawiki": ".wiki",
    "textile": ".textile",
    "docbook": ".xml",
    "jats": ".xml",
    "revealjs": ".html",
    "beamer": ".tex",
}

#: 这些目标需要用 --standalone 才能生成完整文档
_STANDALONE = {"html", "html4", "html5", "chunkedhtml", "latex", "docx", "odt",
               "epub", "epub2", "epub3", "rtf", "pptx", "revealjs", "beamer"}


def available() -> bool:
    return registry.resolve("pandoc") is not None


def version() -> str | None:
    exe = registry.resolve("pandoc")
    if not exe:
        return None
    try:
        return runner.run([str(exe), "--version"], timeout=30).stdout.splitlines()[0].strip()
    except runner.EngineError:
        return None


def format_for_ext(ext: str) -> str | None:
    return EXT_TO_FORMAT.get(ext.lower())


def ext_for_format(fmt: str) -> str:
    return FORMAT_TO_EXT.get(fmt, f".{fmt}")


def convert(
    src: Path | str,
    dst: Path | str,
    *,
    to_format: str | None = None,
    from_format: str | None = None,
    extra_args: list[str] | None = None,
    timeout: int = 600,
) -> Path:
    """用 Pandoc 转换单个文件。

    Args:
        src: 源文件。
        dst: 目标文件路径（扩展名决定 ``-t``，除非显式给 ``to_format``）。
        to_format: 目标格式名。
        from_format: 源格式名；默认按扩展名推断。
        extra_args: 追加的 Pandoc 参数。
    """
    exe = registry.resolve("pandoc")
    if exe is None:
        raise runner.EngineError(
            "Pandoc 未安装，无法转换 Markdown/HTML/EPUB 等格式。"
            "请先运行：pwsh tools\\fetch_engines.ps1 -Only pandoc"
        )

    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    src_fmt = from_format or format_for_ext(src_path.suffix)
    dst_fmt = to_format or format_for_ext(dst_path.suffix)
    if not dst_fmt:
        raise runner.EngineError(f"Pandoc 无法推断目标格式：{dst_path.name}")

    if dst_fmt in {"pdf"}:
        raise runner.EngineError(
            "Pandoc 不能直接生成 PDF，请使用 docpix 的文档转换链路（会自动走 HTML → PDF）。"
        )

    cmd = [str(exe), str(src_path)]
    if src_fmt:
        cmd += ["-f", src_fmt]
    cmd += ["-t", dst_fmt]
    if dst_fmt in _STANDALONE:
        cmd.append("--standalone")
    cmd += ["--resource-path", str(src_path.parent)]
    cmd += ["-o", str(dst_path)]
    if extra_args:
        cmd += list(extra_args)

    runner.run(cmd, timeout=timeout)

    if not dst_path.is_file() or dst_path.stat().st_size == 0:
        raise runner.EngineError(f"Pandoc 未生成输出：{dst_path.name}")
    return dst_path


def extract_text(src: Path | str, *, timeout: int = 300) -> str:
    """把任意 Pandoc 支持的文档抽成纯文本（用于预览 / 搜索）。"""
    exe = registry.resolve("pandoc")
    if exe is None:
        raise runner.EngineError("Pandoc 未安装，无法抽取文本。")
    result = runner.run(
        [str(exe), str(src), "-t", "plain"],
        timeout=timeout,
    )
    return result.stdout
