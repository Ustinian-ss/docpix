# 第三方组件与许可说明（THIRD_PARTY_NOTICES）

docpix 本体以 **MIT** 许可发布（见 [LICENSE](LICENSE)）。本项目不自研格式解析，
而是把成熟引擎以「库调用」或「独立进程」的方式组合起来，因此需要逐一交代。

> 判定原则：**只有链接进本进程、随仓库分发的代码才会影响 docpix 的许可**。
> 以独立进程调用的程序（LibreOffice / Pandoc / Tesseract / qpdf）不是衍生作品；
> 由用户自行 `pip install` 的可选依赖也不随仓库分发。

---

## 1. 直接依赖（Python 包，MIT / BSD / Apache / MPL）

| 组件 | 许可 | 用途 |
| --- | --- | --- |
| FastAPI | MIT | HTTP 框架 |
| Starlette | BSD-3-Clause | ASGI 工具集 |
| Uvicorn | BSD-3-Clause | ASGI 服务器 |
| Pydantic | MIT | 参数校验 |
| python-multipart | Apache-2.0 | multipart 上传解析 |
| Pillow | MIT-CMU | 图片编解码与处理 |
| pillow-heif | BSD-3-Clause | HEIC / AVIF 读写（见下方说明） |
| pypdf | BSD-3-Clause | PDF 页面级操作、加密、文本抽取 |
| pikepdf | MPL-2.0 | PDF 无损重写 / 压缩 / 线性化（内置 qpdf，Apache-2.0） |
| pypdfium2 | Apache-2.0 / BSD-3-Clause | PDF 渲染成位图（内置 PDFium，BSD-3-Clause） |
| reportlab | BSD-3-Clause | 页码 / 水印叠加层 |

### pillow-heif 与 LGPL 说明

`pillow-heif` 自身是 BSD-3-Clause，但它**动态链接**了 libheif / libde265
（LGPL-3.0）。docpix 只把它列为可选依赖、由用户从 PyPI 安装，**不随本仓库分发
任何二进制**，因此不改变 docpix 的 MIT 许可。若你的分发场景对此敏感，可卸载
`pillow-heif`：其余功能不受影响，只是不能读写 HEIC/AVIF。

## 2. 可选依赖：OCR（`pip install -r requirements-ocr.txt`）

| 组件 | 许可 | 说明 |
| --- | --- | --- |
| ocrmypdf | MPL-2.0 | OCR 编排，调用 Tesseract |
| pdfminer.six | MIT | PDF 文本抽取 |
| img2pdf | LGPL-3.0 | ocrmypdf 的图片转 PDF 后端（可选依赖，不随仓库分发） |
| fpdf2 | LGPL-3.0 | ocrmypdf 的 PDF 生成后端（可选依赖，不随仓库分发） |

> 结论：OCR 是**可选安装**，且 `ocrmypdf 16+` 的 PDF/A 与压缩全部由
> pikepdf 完成，**不再需要 Ghostscript**。若不安装 OCR 依赖，docpix 的
> 其余功能完全不受影响。

## 3. 外部引擎（独立进程，不随仓库分发）

由 [`tools/fetch_engines.ps1`](tools/fetch_engines.ps1) 与
[`tools/fetch_tesseract.ps1`](tools/fetch_tesseract.ps1) 从官方渠道下载到项目
`bin/` 目录（F 盘），docpix 只通过 `subprocess` 调用它们。

| 引擎 | 许可 | 用途 | 调用方式 |
| --- | --- | --- | --- |
| LibreOffice | MPL-2.0 | Office 文档高保真互转、HTML→PDF | `soffice.exe --headless --convert-to` |
| Pandoc | **GPL-2.0-or-later** | Markdown / HTML / EPUB / LaTeX 互转 | `pandoc.exe`（独立进程，不与本项目链接、不随仓库分发，因此不传染 MIT） |
| qpdf | Apache-2.0 | PDF 线性化 / 结构修复 | `qpdf.exe` |
| Tesseract OCR | Apache-2.0 | 文字识别 | `tesseract.exe`（ocrmypdf 调用） |
| tessdata / tessdata_best 语言模型 | Apache-2.0 | 识别模型 | 数据文件 |

## 4. 刻意排除的组件（许可红线）

为保证 docpix 整体可以保持 MIT，以下组件**永远不引入**：

| 组件 | 许可 | 为何排除 |
| --- | --- | --- |
| Ghostscript | AGPL-3.0 | 强 copyleft，且 AGPL 对网络服务有额外义务 |
| PyMuPDF (fitz) | AGPL-3.0 | 同上 |
| Poppler (`pdftoppm`/`pdftotext`) | GPL-2.0 | 强 copyleft |

对应的替代方案：

* PDF → 图片：`pypdfium2`（BSD-3-Clause / Apache-2.0）
* PDF 文本抽取：`pypdf`（BSD-3-Clause）
* PDF 压缩 / 线性化 / 结构修复：`pikepdf`（MPL-2.0）、`qpdf`（Apache-2.0）
* PDF/A 转换：`ocrmypdf 16+` 内置转换器（不再调用 Ghostscript）

## 5. 前端

`app/static/` 下的 HTML / CSS / JS 全部手写，**不引用任何 CDN、外部字体或
第三方脚本**，离线可用。
