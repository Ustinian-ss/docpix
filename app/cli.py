"""统一入口：开发时 ``python -m app``，发布时 ``docpix.exe``。

打包成 exe 之后这个入口多两项职责：

1. **必须最先调用** ``multiprocessing.freeze_support()``。ocrmypdf 处理多页 PDF
   时会用多进程，Windows 走 spawn，会重新拉起 ``sys.executable`` —— 也就是
   docpix.exe 自己；只有 freeze_support() 能让这些子进程正确地回到工作进程
   分支，而不是再启动一个 Web 服务。
2. **子进程重入**。``app/engines/ocr.py`` 用
   ``[sys.executable, "-m", "ocrmypdf", ...]`` 调 OCR 引擎；开发时那是
   ``python -m ocrmypdf``，冻结后 ``sys.executable`` 就是 docpix.exe，
   于是这里把 ``-m <模块>`` 解释成「用那个模块跑」，等价于 ``python -m <模块>``。
"""

from __future__ import annotations

import multiprocessing
import runpy
import sys


def _run_module(name: str, argv: list[str]) -> None:
    """等价于 ``python -m <name>``（冻结后用于重入子进程）。"""
    sys.argv = [name, *argv]
    runpy.run_module(name, run_name="__main__", alter_sys=True)


def main() -> None:
    # 必须早于任何 multiprocessing 用法；正常启动时它立即返回。
    multiprocessing.freeze_support()

    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "-m":
        _run_module(args[1], args[2:])
        return

    from .main import main as serve

    serve()
