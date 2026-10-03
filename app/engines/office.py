"""LibreOffice headless 转换引擎。

三个关键点：

1. 必须用 ``-env:UserInstallation=file:///...`` 把用户配置钉在项目
   ``.tmp/soffice`` 里。否则 LibreOffice 会往 ``%APPDATA%``（C 盘）写配置，
   违反「不占用 C 盘」的约束。
2. 同一个 UserInstallation 不能并发启动，因此模块内用一把锁串行化所有调用。
3. ``--convert-to`` 支持一次传入多个输入文件，批量转换时比逐个调用快得多
   （省掉了每次 ~2 秒的启动开销）。
"""

from __future__ import annotations

import threading
from pathlib import Path

from .. import config
from . import registry, runner

_LOCK = threading.Lock()

#: LibreOffice 能「直接吃进去」的输入后缀
INPUT_EXTS = {
    ".doc", ".docx", ".docm", ".dot", ".dotx", ".odt", ".ott", ".rtf", ".txt",
    ".html", ".htm", ".xhtml", ".xml", ".wps",
    ".xls", ".xlsx", ".xlsm", ".xlt", ".ods", ".ots", ".csv", ".tsv", ".dbf",
    ".ppt", ".pptx", ".pptm", ".odp", ".otp", ".pps", ".ppsx",
    ".odg", ".svg", ".fodt", ".fods", ".fodp", ".uot", ".pages", ".numbers", ".key",
}

#: LibreOffice 可输出的目标（键即输出扩展名）
OUTPUT_TARGETS = tuple(config.OFFICE_TARGETS.keys())


def available() -> bool:
    return registry.resolve("libreoffice") is not None


def _profile_uri() -> str:
    d = config.SOFFICE_PROFILE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return "file:///" + d.as_posix()


def _build_cmd(exe: Path, inputs: list[Path], target: str, outdir: Path) -> list[str]:
    filter_name = config.OFFICE_TARGETS.get(target, target)
    return [
        str(exe),
        "--headless",
        "--norestore",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        f"-env:UserInstallation={_profile_uri()}",
        "--convert-to", filter_name,
        "--outdir", str(outdir),
        *[str(p) for p in inputs],
    ]


def convert_many(
    inputs: list[Path | str],
    outdir: Path | str,
    target: str,
    *,
    timeout: int | None = None,
) -> list[Path]:
    """批量转换。

    Args:
        inputs: 源文件列表（同一目录或不同目录均可）。
        outdir: 输出目录。
        target: 目标扩展名，见 :data:`OUTPUT_TARGETS`。
        timeout: 整体超时秒数，默认按文件数放大。

    Returns:
        实际生成的文件路径列表。若一个都没生成则抛 :class:`~app.engines.runner.EngineError`。
    """
    srcs = [Path(p) for p in inputs]
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not srcs:
        return []

    exe = registry.resolve("libreoffice")
    if exe is None:
        raise runner.EngineError(
            "LibreOffice 未安装，无法转换 Office 文档。"
            "请先运行：pwsh tools\\fetch_engines.ps1"
        )

    if target not in config.OFFICE_TARGETS:
        raise runner.EngineError(
            f"LibreOffice 不支持的目标格式：{target}；可选 {', '.join(OUTPUT_TARGETS)}"
        )

    budget = timeout if timeout is not None else max(240, 90 * len(srcs))

    with _LOCK:
        result = runner.run(_build_cmd(exe, srcs, target, out_dir), timeout=budget, check=False)

    produced: list[Path] = []
    missing: list[str] = []
    for src in srcs:
        cand = out_dir / f"{src.stem}.{target}"
        if cand.is_file() and cand.stat().st_size > 0:
            produced.append(cand)
        else:
            missing.append(src.name)

    if not produced:
        detail = (result.stderr or result.stdout or "").strip()
        raise runner.EngineError(
            f"LibreOffice 未生成任何输出（{', '.join(missing[:5])}…）。"
            f"退出码 {result.returncode}；{detail[-500:]}",
            cmd=_build_cmd(exe, srcs, target, out_dir),
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return produced


def convert(src: Path | str, outdir: Path | str, target: str, *, timeout: int | None = None) -> Path:
    """单文件转换，返回输出路径。"""
    produced = convert_many([src], outdir, target, timeout=timeout)
    return produced[0]


def to_pdf(src: Path | str, outdir: Path | str, *, timeout: int | None = None) -> Path:
    """把任意 LibreOffice 支持的文档转成 PDF。"""
    return convert(src, outdir, "pdf", timeout=timeout)
