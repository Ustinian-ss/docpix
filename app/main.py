"""docpix Web 应用入口。

启动方式::

    .venv\\Scripts\\python.exe -m app            # 默认 127.0.0.1:8765
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8765

注意：``config.isolate_temp()`` 必须在任何会创建临时文件的库之前执行，
所以它出现在本模块所有业务 import 之前。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from . import config

# ---- 第一件事：把临时目录锁死在 F 盘（必须在其它 import 之前） ----
config.isolate_temp()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from . import __version__  # noqa: E402
from .engines import registry, runner  # noqa: E402
from .jobs import manager  # noqa: E402
from .routers import inspect as inspect_router  # noqa: E402
from .routers import jobs as jobs_router  # noqa: E402
from .routers import system as system_router  # noqa: E402

log = logging.getLogger("docpix")

STATIC_DIR = config.BUNDLE_DIR / "app" / "static"
if not STATIC_DIR.is_dir():  # 兜底：源码运行时 __file__ 旁边的 static
    STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    manager.start_cleanup()
    missing = registry.missing_required()
    if missing:
        log.warning("缺少必需引擎：%s；请运行 pwsh tools\\fetch_engines.ps1", ", ".join(missing))
    log.info("docpix %s 已启动，项目目录 %s", __version__, config.PROJECT_ROOT)
    try:
        yield
    finally:
        manager.shutdown()


app = FastAPI(
    title="docpix",
    version=__version__,
    description=(
        "本地优先的文档 / 图片转换服务：图片处理（Pillow）、PDF 处理"
        "（pypdf / pikepdf / pypdfium2 / reportlab）、Office 互转"
        "（LibreOffice）、标记语言互转（Pandoc）、OCR（ocrmypdf + Tesseract）。"
        "全部引擎以独立进程调用，docpix 自身保持 MIT 许可。"
    ),
    lifespan=lifespan,
)

app.include_router(system_router.router)
app.include_router(jobs_router.router)
app.include_router(inspect_router.router)


@app.exception_handler(runner.EngineError)
async def _engine_error_handler(_request: Request, exc: runner.EngineError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/api", include_in_schema=False)
def api_root() -> dict:
    return {
        "name": "docpix",
        "version": __version__,
        "license": "MIT",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    page = STATIC_DIR / "index.html"
    if page.is_file():
        return FileResponse(page)
    return JSONResponse(status_code=200, content={"name": "docpix", "hint": "前端文件缺失，请查看 /docs"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon = STATIC_DIR / "favicon.svg"
    if icon.is_file():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse(status_code=204, content=None)


def main() -> None:
    """启动本地服务（``python -m app`` / ``docpix.exe`` 都走这里）。"""
    import uvicorn

    host = os.environ.get("DOCPIX_HOST", "127.0.0.1")
    port = int(os.environ.get("DOCPIX_PORT", "8765"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print()
    print(f"  docpix {__version__}  ·  MIT License")
    print(f"  界面：{url}")
    print(f"  接口文档：{url}docs")
    print(f"  数据目录：{config.PROJECT_ROOT}")
    if not config.IS_PORTABLE:
        print("  （exe 所在目录不可写，运行时数据已放到 %LOCALAPPDATA%\\docpix）")
    print("  按 Ctrl+C 退出")
    print()

    # 打包后的桌面用法：自动把界面打开；用 DOCPIX_NO_BROWSER=1 关掉。
    if os.environ.get("DOCPIX_NO_BROWSER", "") not in ("1", "true", "yes"):
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    # 注意：这里传 app 对象而不是 "app.main:app" 导入串 —— 冻结成 exe 后
    # uvicorn 无法可靠地按名字重新导入模块。
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
