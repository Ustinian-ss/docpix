> **docpix** —— 本地优先的文档 / 图片转换服务。上传即转，产物落在本机，不联网、不上传云端。

# docpix

图片格式互转 · PDF 全套处理 · Office 文档互转 · Markdown 转 PDF · OCR 识别
FastAPI 后端 + 零构建中文前端，全部引擎以**独立进程**方式调用，整体保持 **MIT** 许可。

```
浏览器 (app/static)  ──HTTP──▶  FastAPI (app/main.py)
                                     │
                     ┌───────────────┼────────────────┬──────────────┐
                     ▼               ▼                ▼              ▼
              imageops(Pillow)  pdfops(pypdf/    convert.py      ocr.py
                                pikepdf/pypdfium2) 链路规划器     (Tesseract)
                                                     │
                                        ┌────────────┴────────────┐
                                        ▼                         ▼
                                   pandoc.exe               soffice.exe
```

---

## 1. 功能一览

| 分类 | 能力 |
| --- | --- |
| **图片**（11 个操作） | 格式转换（JPG/PNG/WebP/AVIF/HEIC/BMP/GIF/TIFF/ICO）、缩放（fit/contain/cover/stretch）、压缩、裁剪、旋转、翻转、文字水印、图片水印、清除 EXIF/GPS/ICC、多图拼接、生成 `.ico` 图标 |
| **PDF**（18 个操作） | 合并、拆分（每页/范围/对半/每 N 页）、取页、删页、重排、旋转、压缩、加密、解密、页码、文字水印、图片水印、转图片、图片转 PDF、抽取文本、改元数据、线性化、修复 |
| **文档转换** | 任意「源格式 → 目标格式」，由链路规划器自动拼接 Pandoc 与 LibreOffice 的步骤 |
| **OCR** | 扫描件 PDF → 可搜索 PDF（`ocrmypdf` + Tesseract），图片 → 文本 / PDF；中英日韩等 10 种语言；输出类型（pdf / pdfa-1/2/3 / txt）与语言列表都由后端能力表驱动 |
| **任务系统** | 异步执行、进度轮询、取消、删除、产物下载与在线预览（PDF 首页渲染成 PNG） |

### 转换链路规划器（`app/convert.py`）

引擎各自的能力有限，docpix 负责把多步串成一条链。典型例子：

| 需求 | 实际链路 | 为什么 |
| --- | --- | --- |
| `md → pdf` | `pandoc(md→html)` → `office(html→pdf)` | **Pandoc 不能直接产 PDF**，必须借道 HTML + LibreOffice |
| `pdf → md` | `office(pdf→docx)` → `pandoc(docx→md)` | Pandoc 读不了 PDF |
| `csv → xlsx` | `office(csv→xlsx)` | Pandoc 没有 spreadsheet writer |
| `md → xlsx` | `pandoc(md→html)` → `office(html→xlsx)` | 同上 |
| `pdf → png` | `pypdfium2` 渲染 | 不用 Poppler |
| `png → pdf` | `images_to_pdf` | 不用 Ghostscript |

任何一步失败都会带上引擎原始报错返回，前端以中文提示条展示。

---

## 2. 快速开始（Windows）

### 2.0 方式一：下载 exe（不想装 Python 就用这个）

到 [Releases](../../releases) 下载 `docpix-v1.0.0-win64.zip`（约 67 MB），
解压到任意目录（**建议避开需要管理员权限的路径**，例如放在 `D:\docpix`），
双击 `docpix.exe` 即可，浏览器会自动打开 <http://127.0.0.1:8765>。
压缩包内已有 `使用说明.txt`。

两点说明：

* **外部引擎不随包分发**。想用「转换」和「OCR」，先在同目录执行
  `tools\fetch_engines.ps1` 与 `tools\fetch_tesseract.ps1`（见 2.2）。
  没装也能启动，只是对应功能会提示引擎缺失。
* 运行时数据（任务、缓存、临时文件）都在 exe 旁边的 `var\`、`.cache\`、`.tmp\`，
  删掉即清空；程序**不写 C 盘**（若 exe 所在目录不可写，会退到
  `%LOCALAPPDATA%\docpix`）。

环境变量：`DOCPIX_PORT`（默认 8765）、`DOCPIX_HOST`（默认 127.0.0.1）、
`DOCPIX_NO_BROWSER=1`（启动时不自动开浏览器）。

### 2.1 方式二：源码运行

```powershell
git clone git@github.com:Ustinian-ss/docpix.git
cd docpix
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# OCR 可选
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

### 2.2 获取外部引擎

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch_engines.ps1      # LibreOffice / Pandoc / qpdf
powershell -ExecutionPolicy Bypass -File tools\fetch_tesseract.ps1   # Tesseract + 中文语言包
```

* 全部下载到程序目录的 `bin/`，**不写 C 盘、不改 PATH、不注册系统项**。
* 脚本幂等：已存在的文件会跳过，可用 `-Force` 重下、`-Only libreoffice` 只装一个。
* `msiexec /a` 解包 LibreOffice 需要管理员令牌，脚本会**弹一次 UAC**；用
  `-NoElevate` 可关闭（改成只提示，需自己用管理员 PowerShell 跑）。
* Tesseract 默认走 **conda-forge**（用本机已有的 conda 在项目内建独立前缀），
  官方 UB-Mannheim 安装包作为后备（国内网络可能 403）。
* 引擎不是硬依赖：缺哪个，对应的功能会在「系统」页标红并给出获取命令，其余功能照常可用。

### 2.3 启动

```powershell
.\.venv\Scripts\python.exe -m app
```

打开 <http://127.0.0.1:8765> 即可（`DOCPIX_HOST` / `DOCPIX_PORT` 可改）。

### 2.4 自己打包 exe

```powershell
.\tools\build_exe.ps1            # 产出 dist\docpix\ 与 dist\docpix-v1.0.0-win64.zip
.\tools\build_exe.ps1 -SkipZip   # 只要目录，不压缩
```

打包配置在 `docpix.spec`，里面注释了几个必须手工兜底的地方（`pillow_heif` 是惰性
导入、`libheif-*.dll` 与 `_pillow_heif*.pyd` 装在 site-packages 根目录、
`pdfium.dll` 由 `collect_dynamic_libs` 收集）。

---

## 3. 使用方式

界面分 6 个标签页：**图片 / PDF / 转换 / OCR / 任务 / 系统**。

1. 拖拽或点选文件（图片类操作在列表里用 ↑ ↓ 调整顺序，顺序即拼接/合并顺序）；
2. 选操作、填参数（参数表单是数据驱动的，只显示与当前选择相关的字段）；
3. 点「开始处理」→ 自动跳到「任务」页轮询进度；
4. 完成后可**下载**或**在线预览**（PDF 渲染首页为 PNG，文本直接内联显示）。

### 约定

* **水印**：多文件时**最后 1 个文件是水印图**（图片水印、PDF 水印同理）。
* **合并 / 拼接**：文件顺序 = 页面/图层顺序。
* **页码表达式**：`1-3,5,8-` 这种写法，支持闭区间、单页、到末尾（`8-`）、从头（`-3`）。
* 上传限制：单个文件 2 GB、单批 500 个；任务记录保留 6 小时、产物 24 小时。

---

## 4. HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/system` | 版本、许可、引擎状态、限额、保留策略 |
| `GET` | `/api/system/engines` | 仅引擎清单（含是否可用与获取命令） |
| `GET` | `/api/system/formats` | 能力矩阵：可转换目标、图片/PDF 操作、OCR 语言 |
| `GET` | `/api/system/health` | 健康检查 |
| `POST` | `/api/inspect` | 上传单个文件探查（图片尺寸/EXIF、PDF 页数/加密） |
| `POST` | `/api/jobs` | 提交任务（multipart：`kind`、`op`、`params` JSON、`files`）→ `202` |
| `GET` | `/api/jobs` | 任务列表 |
| `GET` | `/api/jobs/stats` | 任务统计 |
| `GET` | `/api/jobs/{id}` | 任务详情（状态、进度、日志、产物、链路） |
| `POST` | `/api/jobs/{id}/cancel` | 取消 |
| `DELETE` | `/api/jobs/{id}` | 删除任务与产物 |
| `GET` | `/api/jobs/{id}/files/{name}` | 下载产物 |
| `GET` | `/api/jobs/{id}/preview/{name}` | 在线预览（PDF→PNG、图片缩放、文本） |

```bash
# 例：把两张图片合成一个 PDF（kind=pdf 的 images_to_pdf）
curl -X POST http://127.0.0.1:8765/api/jobs \
  -F kind=pdf -F op=images_to_pdf \
  -F 'params={"page_size":"A4","margin_mm":10}' \
  -F files=@a.png -F files=@b.png
```

> 完整的 `kind`/`op`/参数枚举见 `GET /api/system/formats`，前端也据此渲染表单。

---

## 5. 目录结构

```
docpix/
├─ app/
│  ├─ __main__.py        入口（python -m app / PyInstaller 都用它）
│  ├─ cli.py             freeze_support + `-m <模块>` 子进程重入
│  ├─ main.py            FastAPI 入口（先隔离 TEMP，再导入业务模块）
│  ├─ config.py          目录/限额/格式白名单 + isolate_temp() + 打包后路径解析
│  ├─ convert.py         链路规划器（把需求翻译成引擎步骤序列）
│  ├─ jobs.py            任务管理器（线程池、进度、取消、清理）
│  ├─ storage.py         任务目录、文件名清洗、路径穿越防护、过期清理
│  ├─ models.py          Pydantic 模型（含参数校验）
│  ├─ routers/           system / inspect / jobs 三组路由
│  ├─ engines/           registry(探测) runner(执行) office pandoc qpdf→pdfops imageops ocr
│  └─ static/            前端（原生 ES module，无框架、无 CDN）
├─ bin/                  下载的引擎（不进版本库）
├─ packaging/            docpix.ico + version_info.txt（打包资源）
├─ var/jobs/<id>/        任务数据：meta.json + in/ + out/
├─ tools/                env / fetch_engines / fetch_tesseract / build_exe / make_icon
├─ tests/                pytest（86 个用例）
│  └─ e2e/               端到端工具：make_fixtures / exe_smoke / ui_e2e（手工跑）
├─ docpix.spec           PyInstaller 打包配置
├─ CHANGELOG.md          版本更新日志
└─ THIRD_PARTY_NOTICES.md
```

---

## 6. 许可与红线

docpix 本体是 **MIT**（见 [LICENSE](LICENSE)）。为了守住这一点：

* **刻意排除** Ghostscript(AGPL-3.0)、PyMuPDF(AGPL-3.0)、Poppler(GPL-2.0)；
  `tests/test_engines.py` 里有一条用例会扫描源码，防止它们被误引入。
* Pandoc 是 **GPL-2.0+**：docpix 只把它当**独立进程**调用，不链接、不随仓库分发。
* 其余组件（LibreOffice MPL-2.0、qpdf Apache-2.0、Tesseract Apache-2.0、
  Pillow/pypdf/pypdfium2/reportlab 等）许可详见
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 7. 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest                  # 全部（含真实引擎用例）
.\.venv\Scripts\python.exe -m pytest -m "not engines"  # 只跑纯 Python 部分
.\.venv\Scripts\python.exe -m pytest -m engines        # 只跑 LibreOffice/Pandoc/Tesseract 链路
```

* `tests/test_convert_plan.py` 断言链路规划结果（含「Pandoc 没有 xlsx writer」这类回归）。
* `tests/test_imageops.py`、`tests/test_pdfops.py` 覆盖各个引擎函数。
* `tests/test_storage.py` 覆盖文件名清洗、路径穿越、过期清理，以及「边轮询读边写记录」
  的 Windows 回归用例。
* `tests/test_frontend_contract.py` 比对后端 `capability_summary()` 与前端 `app.js`
  的操作/表单定义，防止后端加了能力而前端没有入口。
* `tests/test_api.py` 用 `TestClient` 跑真实的上传 → 任务 → 下载闭环。
* `tests/test_engines.py` 标记为 `engines`，引擎缺失时自动 skip。

上面都是**单元测试**。另有一套**真跑一遍**的端到端工具在 `tests/e2e/`
（不被 pytest 收集，手工执行），发版前务必跑一次：

```powershell
.\.venv\Scripts\python.exe tests\e2e\make_fixtures.py                     # 生成素材
.\.venv\Scripts\python.exe tests\e2e\exe_smoke.py --base-url http://127.0.0.1:8765
.\.venv\Scripts\python.exe tests\e2e\exe_smoke.py --launch dist\docpix --engines   # 打打包产物
node tests\e2e\ui_e2e.mjs                                                 # 无头 Edge 真点界面
```

`exe_smoke.py` 覆盖图片互转、`to_ico`、HEIC 解码、多图拼接、`md→pdf`（两跳链路）、
PDF 预览 / 合并 / 转图片，以及 OCR **两条**链路（图片走 tesseract、PDF 走 ocrmypdf
—— 后者正是冻结环境里最容易坏的一条）。详见 `tests/e2e/README.md`。

---

## 8. 踩过的坑（给未来的自己）

1. **PowerShell 5.1 只认 UTF-8 with BOM**：`tools/*.ps1` 里全是中文注释，存成无 BOM 的
   UTF-8 会被按 ANSI（GBK）解码，直接把语法读崩。新增/修改 `.ps1` 后务必确认文件头是
   `EF BB BF`。
2. **`msiexec /a` 必须提权**：非管理员会以 1603 失败，日志里是
   `2203 ... C:\WINDOWS\Installer\inprogressinstallinfo.ipi`。脚本会自动弹 UAC。
3. **Pandoc 不会产 PDF**：`pandoc --list-output-formats` 里没有 pdf，必须 `md→html→(LibreOffice)→pdf`。
4. **rapidocr-onnxruntime 要求 Python < 3.13**：本机是 3.13，因此 OCR 走
   `ocrmypdf + Tesseract`；纯 Python 后备方案见 `requirements-ocr.txt` 注释。
5. **临时文件全部留在 F 盘**：`config.isolate_temp()` 会改 `TEMP/TMP/TMPDIR`、
   `PIP_CACHE_DIR`、`HF_HOME`、`XDG_CACHE_HOME`，LibreOffice 用
   `-env:UserInstallation=file:///F:/projects/docpix/.tmp/soffice` 独占 profile，
   并且 `office._LOCK` 串行化 `soffice` 调用（并发调用同一个 profile 会互相踢掉）。
6. **pypdf 5+ 的坑**：`add_metadata` 的键必须带前导 `/`；`UserAccessPermissions`
   没有 `NONE` 成员（权限位直接拼整数）。
7. **qpdf 没有 `--replace-input=false`**：写了会打帮助并退出码 2，修复功能已改成
   `qpdf --qdf --object-streams=disable`，失败再退回 pikepdf 的恢复模式。
8. **Windows 上 `os.replace` 会被读侧挡住（最坑的一个）**：任务记录用「临时文件 + 原子
   替换」写 `meta.json`，而前端/接口几乎一直在轮询读它；Windows 的 `os.replace` 遇到目标
   文件正被别的线程打开会直接 `PermissionError: [WinError 5] 拒绝访问`，**不是重试就会
   一直失败**。曾经的表现是：产物已经躺在 `out/` 里，任务却永远停在 `running / 0%`，
   因为 `_flush()` 把 `OSError` 静默吞了。修法三件套：替换带退避重试、临时文件名带
   `pid-线程号`（避免同一任务两次写入互踩）、失败至少留一条 warning 日志。
   压测证据：一边读一边写，修复前 18552 次写入里 13597 次失败（73%），修复后 0 失败。
9. **`to_ico` 的产物扩展名不能跟源文件走**：批处理默认 `dst_ext = 源扩展名`，于是
   ICO 字节被写进 `xxx_out.png`（文件名骗人、MIME 也不对）。已在 `batch()` 里加
   `_BATCH_OUT_EXT = {"to_ico": "ico"}`。
10. **前后端契约最容易漂移**：`image_input` 带不带点、`ocr_languages` 是嵌套还是扁平、
    `to_ico` 后端有前端没入口、`optimize` 前端发 `false` 而后端要 0-3 档……
    现在 `tests/test_frontend_contract.py` 会直接比对 `convert.capability_summary()`
    与 `app/static/js/app.js` 里的操作表，少一个入口就红。
11. **打包成 exe 后 `__file__` 不再是项目目录**：PyInstaller 会把它指向
    `sys._MEIPASS` 临时解包目录，于是 `bin/`（引擎）、`var/`、`.cache/`、`.tmp/`
    全都会跑到临时目录（还是 C 盘）。现在 `config.py` 把「只读资源目录
    (`BUNDLE_DIR` = `_MEIPASS`)」和「可写运行时根目录 (`RUNTIME_ROOT` = exe 旁边)」
    分开，前端从前者的 `app/static` 读，引擎与任务数据放后者。
12. **`sys.executable` 在冻结后是自己**：`ocr.py` 调 ocrmypdf 用的是
    `[sys.executable, "-m", "ocrmypdf"]`，打包后会变成「再启动一个 docpix.exe」。
    解法是入口里把 `-m <模块>` 解释成 `runpy.run_module(..., run_name="__main__")`，
    同时对任意 `-m` 通用（新增引擎不用再改代码）。
13. **ocrmypdf 会用多进程**：Windows 走 spawn，会重新拉起 `sys.executable`，
    所以入口**第一件事**必须是 `multiprocessing.freeze_support()`，
    否则子进程会又开一个 Web 服务（端口占用、CPU 打满）。
14. **三个本地库自动分析收不全**：`pillow_heif` 是惰性导入（藏在
    `imageops._ensure_heif()` 里）静态分析看不见；`_pillow_heif*.pyd` 和
    `libheif-*.dll` 装在 **site-packages 根目录**，靠 delvewheel 的
    `os.add_dll_directory` 加载，冻结后没有那个目录；`pdfium.dll` 是 ctypes 按路径
    加载的。三者都要在 `docpix.spec` 里手工收集 —— 漏了不会报错，只在用到 HEIC 或
    PDF 预览时才炸，所以发布前务必跑一遍 `exe_e2e`（HEIC→PNG、PDF 预览、OCR 各来一发）。
15. **PowerShell 5.1 两个坑（写构建脚本必踩）**：`$ErrorActionPreference='Stop'` 时
    原生程序往 stderr 写东西会被当成异常直接中断（PyInstaller 的 conda 警告就会触发）；
    而且 `>` 与 `Tee-Object` 默认把日志写成 UTF-16。构建脚本里统一走一个包装函数：
    临时改 `Continue`、`Out-String` 收走输出、`Out-File -Encoding utf8` 落盘、
    只 `return $LASTEXITCODE`（否则返回值里混进输出行，`-ne 0` 永远成立）。

---

## 9. 发布流程（维护者）

```powershell
# 1. 改版本号（三处保持一致）
#    app\__init__.py 的 __version__
#    pyproject.toml 的 version
#    packaging\version_info.txt 的 filevers / ProductVersion
# 2. 更新 CHANGELOG.md
# 3. 跑测试
.\.venv\Scripts\python.exe -m pytest
# 4. 构建发布物
.\tools\build_exe.ps1
# 5. 冒烟：解压 dist\docpix\ 到干净目录，双击 docpix.exe 试一遍图片/转换/OCR
# 6. 提交 + 打标签 + 推送
git add -A; git commit -m "release: vX.Y.Z"; git tag -a vX.Y.Z -m "docpix vX.Y.Z"
git push origin main; git push origin vX.Y.Z
# 7. 建 Release 并挂上压缩包
gh release create vX.Y.Z "dist\docpix-vX.Y.Z-win64.zip" --title "docpix vX.Y.Z" --notes-file CHANGELOG.md --verify-tag
```

发布前建议在**干净的临时目录**里跑一遍 `exe_e2e` 那类检查（图片互转、`to_ico`、
`md→pdf`、OCR、PDF 预览各一发）。漏收本地库不会在构建时报错，只在用到
HEIC 或 PDF 预览时才炸 —— 详见第 8 节第 14 条。
