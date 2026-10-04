# 端到端验证（tests/e2e）

单元测试（`pytest`）覆盖的是各引擎函数与路由；这里放的是**真跑一遍**的验证工具，
用来在发版前确认「打包好的程序 + 真实引擎」这条链路没坏。

这些脚本**不被 pytest 收集**（文件名不以 `test_` 开头），需要手动跑。

## 一次性准备

```powershell
# 1. 素材（生成到 tests/e2e/fixtures/，不进版本库）
.\.venv\Scripts\python.exe tests\e2e\make_fixtures.py

# 2. 引擎（转换 / OCR 需要；图片与 PDF 处理不需要）
powershell -ExecutionPolicy Bypass -File tools\fetch_engines.ps1
powershell -ExecutionPolicy Bypass -File tools\fetch_tesseract.ps1

# 3. 后端
.\.venv\Scripts\python.exe -m app
```

## 1. 服务冒烟（HTTP 层，覆盖全部引擎链路）

```powershell
# 打开发环境（默认 8766，可指定）
.\.venv\Scripts\python.exe tests\e2e\exe_smoke.py --base-url http://127.0.0.1:8765

# 打打包产物：复制到 .tmp\e2e-packaged\ 再启动，模拟用户「解压即用」
.\.venv\Scripts\python.exe tests\e2e\exe_smoke.py --launch dist\docpix --engines
```

覆盖：版本/许可、四个引擎探测、PNG→WebP、`to_ico`（扩展名 + ICO 魔数）、
HEIC 解码（libheif）、三图拼接、`md→pdf`（pandoc + LibreOffice 两跳，并校验
记录里的链路）、PDF 预览（pdfium）、PDF 合并（pikepdf）、PDF 转图片、
OCR（`ocrmypdf` 子进程重入）**并校验产物的可检索文本层**、任务列表。

`--launch` 模式下工作目录（含 `smoke.log`）会保留，便于排查。

## 2. 界面端到端（无头 Edge + CDP，真的点界面）

```powershell
# 起无头 Edge（开着调试端口并打开页面）
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
    --headless=new --remote-debugging-port=9222 --user-data-dir=$env:TEMP\docpix-edge `
    http://127.0.0.1:8765/

# 另一终端跑断言（58 项：标签页、操作下拉、参数表单、真实上传→任务→产物、预览弹窗…）
node tests\e2e\ui_e2e.mjs
```

可配环境变量：`DOCPIX_ORIGIN`（默认 `http://127.0.0.1:8765`）、
`DOCPIX_CDP`（默认 `http://127.0.0.1:9222`）、`DOCPIX_FIXTURES`（默认本目录 `fixtures/`）、
`DOCPIX_E2E_LOG`（把逐条结果同时写一份到文件，便于中途被中断时查看）。

需要 Node（本机是 `F:\nodejs\node.exe`，PATH 里的 `node` 可能是坏的 shim）。

## 为什么这些检查值得留着

打包（PyInstaller）有若干「不报错但会坏」的坑，只有真跑才会暴露：

* `pillow_heif` 是惰性导入 + `libheif-*.dll` 在 site-packages 根目录 → 漏收后
  **只在解码 HEIC 时**才炸；
* `pdfium.dll` 由 ctypes 按路径加载 → 漏收后**只在 PDF 预览/转图片时**才炸；
* `ocrmypdf` 靠 `[sys.executable, "-m", "ocrmypdf"]` 调用，冻结后需要入口做
  `-m <模块>` 重入 → 漏了之后 **OCR 会变成「再启动一个 docpix」**；
* ocrmypdf 用多进程，入口少了 `multiprocessing.freeze_support()` 会重复起服务。

所以**每次发版前**至少跑一遍 `exe_smoke.py --launch ... --engines`。
