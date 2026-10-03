# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，Windows）。

用法::

    .\\.venv\\Scripts\\python.exe -m PyInstaller docpix.spec --noconfirm

产物是 ``dist/docpix/`` 目录：``docpix.exe`` + ``_internal/``（Python 运行时与
全部依赖）。外部引擎（LibreOffice / Pandoc / qpdf / Tesseract）**不打包**，
运行时由 ``tools/fetch_engines.ps1`` 下载到 exe 旁边的 ``bin/``：

* LibreOffice 解包后 1.6 GB 上下，塞进发布物不现实；
* Pandoc 是 GPL-2.0+，只以独立进程调用、不随仓库分发，许可上更干净。

几处需要手工兜底的地方（自动分析覆盖不到）：

1. ``pillow_heif`` 是**惰性导入**（``imageops._ensure_heif()`` 里才 import），
   静态分析看不到，必须写进 hiddenimports。
2. ``_pillow_heif*.pyd`` 与 ``libheif-*.dll`` 装在 **site-packages 根目录**，
   靠 delvewheel 补丁 ``os.add_dll_directory(site-packages)`` 加载；冻结后没有
   site-packages，必须把 DLL 收进包根。
3. ``pypdfium2_raw/pdfium.dll`` 是 ctypes 按路径加载的，用
   ``collect_dynamic_libs`` 显式收集。
4. ocrmypdf 自带 PyInstaller 钩子（``pyinstaller40`` 入口点），collect_all 会把
   它的插件与数据一起带上。
"""

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

# --------------------------------------------------------------- 静态资源
datas = [("app/static", "app/static")]

# ------------------------------------------------- 带本地库 / 插件的重包
HEAVY_PACKAGES = (
    "ocrmypdf",      # 自带 pyinstaller40 钩子
    "pypdfium2",     # PDF 渲染（PDFium）
    "pikepdf",       # PDF 结构处理（静态链接 qpdf）
    "pillow_heif",   # HEIC / AVIF
    "img2pdf",       # ocrmypdf 可选后端
    "fpdf",          # ocrmypdf 可选后端
    "pdfminer",      # 文本抽取（带 cmap 数据）
    "fontTools",     # ocrmypdf PDF/A 字体处理
    "uharfbuzz",     # 文本整形
    "reportlab",     # 生成 PDF
    "rich",          # 命令行输出
    "pydantic",      # 校验（带编译扩展）
)

binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

for pkg in HEAVY_PACKAGES:
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# PDFium 动态库（ctypes 按路径加载）
binaries += collect_dynamic_libs("pypdfium2_raw")

# pillow-heif：惰性导入 + site-packages 根目录下的 pyd/dll
hiddenimports += ["pillow_heif", "_pillow_heif"]
_site = os.path.join(sys.prefix, "Lib", "site-packages")
for _pattern in ("_pillow_heif*.pyd", "libheif-*.dll"):
    for _path in glob.glob(os.path.join(_site, _pattern)):
        binaries.append((_path, "."))

# uvicorn 的子模块是按字符串动态选的，静态分析容易漏
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "app.cli",
    "app.main",
    "app.routers.system",
    "app.routers.jobs",
    "app.routers.inspect",
]

a = Analysis(
    ["app/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "pytest",
        "IPython",
        "notebook",
        "PyInstaller",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="docpix",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon="packaging/docpix.ico",
    version="packaging/version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="docpix",
)
