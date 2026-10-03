"""外部引擎登记与探测。

docpix 自身只做编排，真正的重活交给成熟的上游引擎。所有引擎都必须是
宽松许可（MIT / BSD / Apache-2.0 / MPL-2.0），以便本项目整体保持 MIT。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .. import config


@dataclass(frozen=True)
class Engine:
    key: str
    name: str
    license: str
    homepage: str
    #: 相对 ``bin/`` 的可执行文件候选路径
    local_paths: tuple[str, ...]
    #: 在系统 PATH 中查找的可执行文件名
    path_names: tuple[str, ...]
    required: bool
    purpose: str
    #: 可选：随引擎一起提供的语言数据目录（相对可执行文件所在目录）
    extra: dict = field(default_factory=dict)


ENGINES: tuple[Engine, ...] = (
    Engine(
        key="libreoffice",
        name="LibreOffice",
        license="MPL-2.0",
        homepage="https://www.libreoffice.org/",
        local_paths=("libreoffice/program/soffice.exe",),
        path_names=("soffice.exe", "soffice"),
        required=True,
        purpose="Office 文档（doc/docx/xls/xlsx/ppt/pptx/odt/odp/rtf）与 PDF 的高保真互转",
    ),
    Engine(
        key="pandoc",
        name="Pandoc",
        license="GPL-2.0-or-later",
        homepage="https://pandoc.org/",
        local_paths=("pandoc/pandoc.exe",),
        path_names=("pandoc.exe", "pandoc"),
        required=False,
        purpose="Markdown / HTML / EPUB / LaTeX / RST 等标记语言的互转（独立进程调用，不影响本项目 MIT 许可）",
    ),
    Engine(
        key="qpdf",
        name="qpdf",
        license="Apache-2.0",
        homepage="https://qpdf.readthedocs.io/",
        local_paths=("qpdf/bin/qpdf.exe",),
        path_names=("qpdf.exe", "qpdf"),
        required=False,
        purpose="PDF 线性化、结构修复与无损重写",
    ),
    Engine(
        key="tesseract",
        name="Tesseract OCR",
        license="Apache-2.0",
        homepage="https://github.com/tesseract-ocr/tesseract",
        # 兼容两种安装布局：Inno Setup 直装（bin\tesseract\）与 conda 前缀
        # （bin\tesseract\Library\bin\）
        local_paths=(
            "tesseract/tesseract.exe",
            "tesseract/Library/bin/tesseract.exe",
        ),
        path_names=("tesseract.exe", "tesseract"),
        required=False,
        purpose="扫描件 OCR 与可搜索 PDF 生成",
        extra={"tessdata": ("tessdata",)},
    ),
)

_BY_KEY = {e.key: e for e in ENGINES}


def get(key: str) -> Engine | None:
    return _BY_KEY.get(key)


def _candidate_dirs() -> list[Path]:
    dirs = [config.BIN_DIR]
    for e in ENGINES:  # 让 soffice 等能顺带找到同目录 DLL 依赖
        for rel in e.local_paths:
            dirs.append((config.BIN_DIR / rel).parent)
    return dirs


def resolve(key: str) -> Path | None:
    """定位引擎可执行文件：先看项目 ``bin/``，再退回系统 PATH。"""
    engine = _BY_KEY.get(key)
    if engine is None:
        return None

    for rel in engine.local_paths:
        p = config.BIN_DIR / rel
        if p.is_file():
            return p

    for name in engine.path_names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def tessdata_dir() -> Path | None:
    """定位 tessdata 语言包目录。"""
    exe = resolve("tesseract")
    if not exe:
        return None
    for cand in (
        config.BIN_DIR / "tesseract" / "tessdata",
        exe.parent / "tessdata",
        exe.parent.parent / "share" / "tessdata",
    ):
        if cand.is_dir():
            return cand
    return None


def installed_langs() -> list[str]:
    d = tessdata_dir()
    if not d:
        return []
    return sorted(p.stem for p in d.glob("*.traineddata"))


def detect() -> list[dict]:
    """探测所有引擎状态，供「系统自检」页面展示。"""
    out: list[dict] = []
    for e in ENGINES:
        path = resolve(e.key)
        item = {
            "key": e.key,
            "name": e.name,
            "license": e.license,
            "homepage": e.homepage,
            "purpose": e.purpose,
            "required": e.required,
            "available": path is not None,
            "path": str(path) if path else None,
        }
        if e.key == "tesseract":
            langs = installed_langs()
            item["languages"] = langs
            item["tessdata"] = str(tessdata_dir()) if tessdata_dir() else None
        out.append(item)
    return out


def missing_required() -> list[str]:
    return [e.key for e in ENGINES if e.required and resolve(e.key) is None]
