# 更新日志

本项目的版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v1.0.0（2026-10-03）

首个正式版本。定位：**本地优先**的文档 / 图片转换服务 —— 默认只监听
`127.0.0.1`，文件不出本机，引擎全部以独立进程调用。

### 功能

* **图片（11 个操作）**：格式转换（JPG/PNG/WebP/AVIF/HEIC/BMP/GIF/TIFF/ICO）、
  缩放（fit/contain/cover/stretch）、压缩、裁剪、旋转、翻转、文字水印、图片水印、
  清除 EXIF/GPS/ICC、多图拼接、生成 `.ico` 图标。
* **PDF（18 个操作）**：合并、拆分（每页/范围/对半/每 N 页）、取页、删页、重排、
  旋转、压缩、加密、解密、页码、文字水印、图片水印、转图片、图片转 PDF、
  抽取文本、改元数据、线性化、修复。
* **文档转换（链路规划器）**：任意「源格式 → 目标格式」自动拼接步骤。
  Markdown → PDF 走 `pandoc(md→html)` → `LibreOffice(html→pdf)`，
  因为 Pandoc 本身不能直接产 PDF。
* **OCR**：扫描件 PDF / 图片 → 可搜索 PDF（`ocrmypdf` + Tesseract），
  输出类型 `pdf / pdfa / pdfa-1 / pdfa-2 / pdfa-3 / txt`，语言列表按实际安装的
  tessdata 展示（中英日韩等 10 种）。
* **任务系统**：异步执行（并发 4）、进度轮询、取消、删除、产物下载与在线预览
  （PDF 首页渲染成 PNG）。
* **中文界面**：原生 ES module，无框架、无 CDN、无构建步骤，6 个标签页。
* **引擎按需下载**：`tools/fetch_engines.ps1`（LibreOffice / Pandoc / qpdf，幂等、
  `curl -C -` 断点续传）、`tools/fetch_tesseract.ps1`（Tesseract + `chi_sim`）。

### 发布物

* Windows exe（PyInstaller onedir）：`docpix.exe` 16.9 MB，解包后 150.7 MB，
  免 Python 环境，双击即用。外部引擎不随包分发，首次运行时按需下载到 exe 旁的
  `bin/`。打包脚本：`tools/build_exe.ps1`。

### 修复（开发期间发现并解决）

* **Windows 上任务永远停在 `running / 0%`**：任务记录用「临时文件 + `os.replace`」
  原子写入，而前端一直在轮询读它；Windows 的 `os.replace` 遇到目标文件被其它线程
  打开会抛 `PermissionError [WinError 5]`，而写入失败被静默吞掉 —— 产物已经生成，
  界面却永远显示进行中。压测复现：18552 次写入失败 13597 次（73%）。
  修法：替换带退避重试、临时文件名带 `pid-线程号`、失败至少记一条 warning。
  回归用例见 `tests/test_storage.py`。
* **`to_ico` 的产物扩展名跟着源文件走**，导致 ICO 字节被写进 `xxx_out.png`
  （文件名与 MIME 都不对）。现在按操作映射输出扩展名。
* **OCR 的 `optimize` 是复选框**，前端发 `false`、后端要 0-3 档，语义对不上；
  现在是有中文说明的 0/1/2/3 下拉，后端 `Field(ge=0, le=3)` 校验。
* **OCR 输出类型前端硬编码 3 项**，漏了 `pdfa-1/2/3`；现在完全由后端能力表驱动。
* 新增 `tests/test_frontend_contract.py`：比对后端 `capability_summary()` 与前端
  `app/static/js/app.js` 的操作定义，防止「后端有、前端没入口」这类漂移。

### 许可

MIT。刻意避开 Ghostscript(AGPL-3.0)、PyMuPDF(AGPL-3.0)、Poppler(GPL-2.0)，
以保证整体许可干净；Pandoc 是 GPL-2.0+，仅以独立进程调用且不随仓库分发。
详见 `THIRD_PARTY_NOTICES.md`。
