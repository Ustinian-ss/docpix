#Requires -Version 5.1
<#
.SYNOPSIS
    打包 docpix 的 Windows 发布物（onedir + zip）。

.DESCRIPTION
    流程：
      1. 检查/安装 PyInstaller；
      2. 生成图标（packaging\docpix.ico 缺失时）；
      3. 跑 docpix.spec 得到 dist\docpix\（docpix.exe + _internal\）；
      4. 把 tools\ 与文档复制进发布目录（**不含**外部引擎）；
      5. 压缩成 dist\docpix-v<版本>-win64.zip。

    外部引擎（LibreOffice / Pandoc / qpdf / Tesseract）不随发布物分发：
    用户首次运行时用 tools\fetch_engines.ps1 与 tools\fetch_tesseract.ps1
    下载到 exe 旁边的 bin\ 目录。

.PARAMETER SkipZip
    只构建目录，不压缩。
#>
[CmdletBinding()]
param(
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot          # 仓库根目录
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Dist = Join-Path $Root 'dist'
$AppDir = Join-Path $Dist 'docpix'
$BuildLog = Join-Path $Root '.cache\buildlog'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

# PowerShell 5.1 有个坑：原生程序往 stderr 写东西时，在 $ErrorActionPreference='Stop'
# 下会被当成 NativeCommandError 直接中断脚本（PyInstaller 的 conda 警告就会触发）。
# 所以所有原生命令都走这里：临时改成 Continue，合并所有输出流，再显式看退出码。
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$LogFile
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # 注意：这里必须把程序输出「消费掉」，只 return 退出码 ——
        # 否则调用方拿到的是「输出行数组 + 退出码」，$exit -ne 0 会永远成立。
        $text = (& $Exe @Arguments 2>&1 | Out-String)
        if ($LogFile) {
            # 显式 UTF-8：PowerShell 5.1 的 > 与 Tee-Object 默认写成 UTF-16
            $text | Out-File -FilePath $LogFile -Encoding utf8
        }
        Write-Host $text
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

# ------------------------------------------------------------------ 前置检查
Write-Step '检查构建环境'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "找不到虚拟环境解释器：$Python`n请先执行：python -m venv .venv"
}

# 版本号从 app\__init__.py 读，避免两处硬编码（用 Select-String 以免嵌套引号）
$InitPy = Join-Path $Root 'app\__init__.py'
$VersionMatch = Select-String -LiteralPath $InitPy -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $VersionMatch) { throw "无法从 $InitPy 读取版本号" }
$Version = $VersionMatch.Matches[0].Groups[1].Value
Write-Host "    版本号    : $Version"
Write-Host "    仓库根目录: $Root"
Write-Host "    解释器    : $Python"

if ((Invoke-Native -Exe $Python -Arguments @('-c', 'import PyInstaller')) -ne 0) {
    Write-Host '    未安装 PyInstaller，正在安装…'
    if ((Invoke-Native -Exe $Python -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', 'pyinstaller')) -ne 0) {
        throw 'PyInstaller 安装失败'
    }
}

$Icon = Join-Path $Root 'packaging\docpix.ico'
if (-not (Test-Path -LiteralPath $Icon)) {
    Write-Host '    图标缺失，重新生成…'
    if ((Invoke-Native -Exe $Python -Arguments @((Join-Path $Root 'tools\make_icon.py'))) -ne 0) {
        throw '图标生成失败'
    }
}

New-Item -ItemType Directory -Force -Path $BuildLog | Out-Null

# --------------------------------------------------------------------- 构建
Write-Step 'PyInstaller 构建（onedir）'

$LogFile = Join-Path $BuildLog 'pyinstaller.log'
$BuildArgs = @('-m', 'PyInstaller', 'docpix.spec', '--noconfirm', '--log-level', 'INFO')
Push-Location $Root
try {
    $buildExit = Invoke-Native -Exe $Python -Arguments $BuildArgs -LogFile $LogFile
} finally {
    Pop-Location
}
if ($buildExit -ne 0) {
    throw "PyInstaller 构建失败（退出码 $buildExit），日志：$LogFile"
}

$ExePath = Join-Path $AppDir 'docpix.exe'
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "构建结束但没有找到 $ExePath"
}

# --------------------------------------------------- 组装发布目录（+ 文档）
Write-Step '组装发布目录'

foreach ($item in @('tools', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')) {
    $src = Join-Path $Root $item
    if (-not (Test-Path -LiteralPath $src)) { continue }
    $dst = Join-Path $AppDir $item
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
    Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
}

# 发布目录里的引擎是空的：建个占位说明，避免用户以为缺文件
$BinDir = Join-Path $AppDir 'bin'
if (-not (Test-Path -LiteralPath $BinDir)) {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
}

$Readme = @"
docpix $Version —— 解压即用版
==================================================

1. 双击 docpix.exe，浏览器会自动打开 http://127.0.0.1:8765/
   （第一次启动约 2-5 秒；关闭那个黑色命令行窗口即退出）

2. 想用 Office 互转 / Markdown 转 PDF / OCR，需要先下载外部引擎。
   在本目录打开 PowerShell，执行：

       powershell -ExecutionPolicy Bypass -File tools\fetch_engines.ps1
       powershell -ExecutionPolicy Bypass -File tools\fetch_tesseract.ps1

   引擎会装到本目录的 bin\ 里（LibreOffice 约 1.6 GB）。不装也可以启动，
   但「转换」和「OCR」标签页会提示引擎缺失。

3. 所有任务数据都在本目录的 var\ 下，删除 var\ 即可清空历史。
   程序不会写入 C 盘（除非本目录不可写，此时会退到 %LOCALAPPDATA%\docpix）。

环境变量（可选）：
    DOCPIX_PORT=8765        换端口
    DOCPIX_HOST=127.0.0.1   监听地址
    DOCPIX_NO_BROWSER=1     启动时不自动打开浏览器

许可：MIT。外部引擎各自遵循其上游许可，详见 THIRD_PARTY_NOTICES.md
"@
Set-Content -LiteralPath (Join-Path $AppDir '使用说明.txt') -Value $Readme -Encoding UTF8

# ------------------------------------------------------------------- 压缩
if (-not $SkipZip) {
    Write-Step '压缩发布包'
    $ZipName = "docpix-v$Version-win64.zip"
    $ZipPath = Join-Path $Dist $ZipName
    if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
    Compress-Archive -Path (Join-Path $AppDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal
    $ZipMB = [math]::Round((Get-Item -LiteralPath $ZipPath).Length / 1MB, 1)
    Write-Host "    发布包：$ZipPath（$ZipMB MB）"
}

# ------------------------------------------------------------------- 汇总
Write-Step '完成'
$ExeMB = [math]::Round((Get-Item -LiteralPath $ExePath).Length / 1MB, 1)
$TotalMB = [math]::Round(((Get-ChildItem -LiteralPath $AppDir -Recurse -File |
    Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Host "    $ExePath（$ExeMB MB）"
Write-Host "    发布目录合计：$TotalMB MB"
Write-Host ''
Write-Host '    下一步：把 dist\docpix\ 整个目录拷给用户，或直接发 zip。' -ForegroundColor Green
