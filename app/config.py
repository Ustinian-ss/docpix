"""全局配置：路径、限额、以及最重要的一件事 —— 把临时目录锁死在程序目录内。

docpix 的硬性约束是「不占用 C 盘空间」。Python 的 tempfile、pip 缓存、
LibreOffice 的用户配置、HuggingFace 模型缓存默认都会写进
``C:\\Users\\<user>\\AppData``。这里在导入期就把它们全部重定向到程序目录内，
任何子进程（soffice / pandoc / qpdf / tesseract）都会继承这些环境变量。

打包成 exe 后有两套路径，必须分清：

* :data:`BUNDLE_DIR` —— **只读资源**（前端静态文件）。开发时是仓库根目录，
  打包后是 PyInstaller 的 ``sys._MEIPASS`` 临时解包目录。
* :data:`RUNTIME_ROOT` / :data:`PROJECT_ROOT` —— **可写运行时根目录**。
  开发时是仓库根目录；打包后是 exe 所在目录，于是 ``bin/``（外部引擎）、
  ``var/``（任务数据）、``.cache/``、``.tmp/`` 全都跟着 exe 走，不会被写进
  临时解包目录。exe 旁边不可写时（例如放在 Program Files）退到
  ``%LOCALAPPDATA%\\docpix``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 是否运行在 PyInstaller 打包出来的 exe 里
IS_FROZEN = bool(getattr(sys, "frozen", False))


def _pick_runtime_root() -> tuple[Path, bool]:
    """返回 (可写根目录, 是否是便携模式)。

    便携模式 = 直接放在 exe 旁边（解压即用）；否则退到用户目录。
    """
    if not IS_FROZEN:
        return Path(__file__).resolve().parent.parent, True
    beside = Path(sys.executable).resolve().parent
    try:
        probe = beside / ".docpix-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return beside, True
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "docpix", False


#: 只读资源目录（前端静态文件等）
BUNDLE_DIR = (
    Path(getattr(sys, "_MEIPASS", "")) if IS_FROZEN
    else Path(__file__).resolve().parent.parent
)

_RUNTIME_ROOT, IS_PORTABLE = _pick_runtime_root()

#: 可写运行时根目录（兼容旧名字：全项目都用 PROJECT_ROOT 表示根目录）
RUNTIME_ROOT = _RUNTIME_ROOT
PROJECT_ROOT = _RUNTIME_ROOT

BIN_DIR = PROJECT_ROOT / "bin"
VAR_DIR = PROJECT_ROOT / "var"
CACHE_DIR = PROJECT_ROOT / ".cache"
TMP_DIR = PROJECT_ROOT / ".tmp"

IN_DIR = VAR_DIR / "in"
OUT_DIR = VAR_DIR / "out"
JOBS_DIR = VAR_DIR / "jobs"

SOFFICE_PROFILE_DIR = TMP_DIR / "soffice"

_ALL_DIRS = (
    BIN_DIR,
    VAR_DIR,
    IN_DIR,
    OUT_DIR,
    JOBS_DIR,
    CACHE_DIR,
    CACHE_DIR / "downloads",
    CACHE_DIR / "pip",
    CACHE_DIR / "pycache",
    CACHE_DIR / "huggingface",
    CACHE_DIR / "models",
    TMP_DIR,
    TMP_DIR / "pip",
    TMP_DIR / "build",
    TMP_DIR / "soffice",
    TMP_DIR / "work",
)

# --------------------------------------------------------------------- 限额
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024      # 单文件 2 GB
MAX_BATCH_FILES = 500                           # 单次批处理文件数
JOB_RETENTION_SECONDS = 6 * 60 * 60             # 任务记录保留 6 小时
OUTPUT_RETENTION_SECONDS = 24 * 60 * 60         # 产物保留 24 小时
MAX_CONCURRENT_JOBS = 4

# ----------------------------------------------------------------- 扩展名表
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".avif", ".heic", ".heif",
    ".bmp", ".gif", ".tif", ".tiff", ".ico", ".jp2", ".jxl", ".ppm", ".pgm",
}

DOC_EXTS = {
    ".doc", ".docx", ".docm", ".odt", ".ott", ".rtf", ".txt", ".html", ".htm",
    ".xhtml", ".md", ".markdown", ".epub", ".tex", ".rst", ".org",
    ".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".tsv",
    ".ppt", ".pptx", ".pptm", ".odp", ".odg", ".odf",
}

PDF_EXTS = {".pdf"}

SOURCE_EXTS = IMAGE_EXTS | DOC_EXTS | PDF_EXTS

# LibreOffice 的 --convert-to 目标名（用它自带的过滤器名，保真度最好）
OFFICE_TARGETS = {
    "pdf": "pdf",
    "docx": "docx:MS Word 2007 XML",
    "doc": "doc:MS Word 97",
    "odt": "odt:writer8",
    "rtf": "rtf:Rich Text Format",
    "txt": "txt:Text (encoded):UTF8",
    "html": "html:HTML (StarWriter)",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "xls": "xls:MS Excel 97",
    "ods": "ods:calc8",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false,-1",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "odp": "odp:impress8",
}

# Pandoc 能直接读写的文本型格式
PANDOC_FORMATS = {
    "md", "markdown", "gfm", "commonmark", "html", "htm", "docx", "odt", "epub",
    "latex", "tex", "rst", "org", "txt", "rtf", "ipynb", "json", "mediawiki",
    "opml", "typst", "asciidoc", "textile", "csv", "tsv", "pptx", "xlsx",
}

# 需要写进 OCR 的语言包代码
OCR_LANGS = {
    "chi_sim": "简体中文",
    "chi_tra": "繁体中文",
    "eng": "English",
    "jpn": "日本語",
    "kor": "한국어",
    "fra": "Français",
    "deu": "Deutsch",
    "rus": "Русский",
    "spa": "Español",
}


def ensure_dirs() -> None:
    """创建全部运行期目录（幂等）。"""
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def isolate_temp() -> None:
    """把临时目录与各类缓存重定向到项目目录，确保不写入 C 盘。

    必须在任何会创建临时文件的库被使用之前调用（见 ``app.main``）。
    """
    ensure_dirs()

    os.environ["TEMP"] = str(TMP_DIR)
    os.environ["TMP"] = str(TMP_DIR)
    os.environ["TMPDIR"] = str(TMP_DIR)

    os.environ["PIP_CACHE_DIR"] = str(CACHE_DIR / "pip")
    os.environ["PYTHONPYCACHEPREFIX"] = str(CACHE_DIR / "pycache")
    os.environ["HF_HOME"] = str(CACHE_DIR / "huggingface")
    os.environ["XDG_CACHE_HOME"] = str(CACHE_DIR)
    os.environ["DOCPIX_SOFFICE_PROFILE"] = str(SOFFICE_PROFILE_DIR)


def temp_dir() -> Path:
    """返回一个每次调用都新建的独立临时目录（用于单次任务隔离）。"""
    ensure_dirs()
    import tempfile

    return Path(tempfile.mkdtemp(prefix="job-", dir=str(TMP_DIR)))


def human_size(num: int) -> str:
    step = 1024.0
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    return f"{value:.1f} PB"
