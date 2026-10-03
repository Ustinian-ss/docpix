<#
  docpix Tesseract OCR 获取脚本
  ------------------------------------------------------------------
  为什么单独一个脚本：
    Tesseract 是系统级可执行文件 + 语言包，装法与 Python 依赖完全不同；
    ocrmypdf 只负责“把 OCR 结果写回 PDF”，真正的识别由 Tesseract 完成。

  安装策略（按顺序尝试）：
    1. conda：用 F 盘上已有的 conda 在项目内建一个**独立前缀**
       bin\tesseract（含 Library\bin\tesseract.exe），不写 C 盘、不需要管理员。
       —— 这是默认且推荐的方式。
    2. installer：UB Mannheim 官方 5.x 安装包（静默安装到 bin\tesseract）。
       需要管理员权限（会自动弹一次 UAC）；国内网络访问
       digi.bib.uni-mannheim.de 可能 403，此时请用默认的 conda 方式。

  语言包：默认下载 tessdata_best 的 chi_sim / eng / osd（神经网模型，中文
  识别质量明显好于 fast）。可用 -Quality fast|standard|best 切换来源仓库。

  用法：
    pwsh -File tools\fetch_tesseract.ps1                 # conda + 最佳中文模型
    pwsh -File tools\fetch_tesseract.ps1 -Force          # 重装并重下语言包
    pwsh -File tools\fetch_tesseract.ps1 -Langs chi_sim,chi_tra,eng,osd
    pwsh -File tools\fetch_tesseract.ps1 -Method installer
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [ValidateSet('auto', 'conda', 'installer')][string]$Method = 'auto',
    [ValidateSet('fast', 'standard', 'best')][string]$Quality = 'best',
    [string[]]$Langs = @('chi_sim', 'eng', 'osd'),
    [ValidateSet('auto', 'off', 'on')][string]$Proxy = 'auto',
    [string]$ProxyUrl = '',
    [string]$InstallerUrl = 'https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.5.0.20241111.exe'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'env.ps1')

$BinDir  = $env:DOCPIX_BIN
$DlDir   = Join-Path $env:DOCPIX_CACHE 'downloads'
$DestDir = Join-Path $BinDir 'tesseract'
$Repo    = @{ fast = 'tessdata_fast'; standard = 'tessdata'; best = 'tessdata_best' }[$Quality]

function Say-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Say-Ok($m)   { Write-Host "    [OK]  $m" -ForegroundColor Green }
function Say-Warn($m) { Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Say-Info($m) { Write-Host "    $m" -ForegroundColor DarkGray }

Write-Host 'docpix Tesseract OCR 获取' -ForegroundColor White
Say-Info "安装目标 : $DestDir"
Say-Info "语言包   : $Repo （$($Langs -join ', ')）"

# ---------------------------------------------------------------- 代理探测
$script:CurlProxyArg = @()
if ($Proxy -ne 'off') {
    if ($ProxyUrl) {
        $script:CurlProxyArg = @('-x', $ProxyUrl)
    } else {
        $port = 7897
        $cfg = Join-Path $env:APPDATA 'io.github.clash-verge-rev.clash-verge-rev\verge.yaml'
        if (Test-Path -LiteralPath $cfg) {
            $m = Select-String -LiteralPath $cfg -Pattern '^\s*verge_mixed_port:\s*(\d+)' | Select-Object -First 1
            if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
        }
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', $port); $c.Close()
            $ProxyUrl = "http://127.0.0.1:$port"
            $script:CurlProxyArg = @('-x', $ProxyUrl)
            Say-Info "使用代理 $ProxyUrl"
        } catch {
            Say-Info '代理不可用，直连下载'
        }
    }
}

# ---------------------------------------------------------------- 下载实现
function Invoke-Download {
    param([string]$Url, [string]$OutFile)

    if ((Test-Path -LiteralPath $OutFile) -and -not $Force) {
        $sz = (Get-Item -LiteralPath $OutFile).Length
        if ($sz -gt 0) { Say-Info "已存在，跳过：$(Split-Path -Leaf $OutFile)"; return $true }
    }

    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    $leaf = Split-Path -Leaf $OutFile
    Say-Info "下载 $leaf"

    if (Test-Path -LiteralPath $curl) {
        $curlArgs = @('-L', '--fail', '--retry', '5', '--retry-delay', '3', '--retry-all-errors',
                      '-C', '-', '-o', $OutFile, '--connect-timeout', '20')
        $curlArgs += $script:CurlProxyArg
        $curlArgs += $Url
        & $curl @curlArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $OutFile)) {
            Say-Ok ("$leaf  {0:N1} MB" -f ((Get-Item -LiteralPath $OutFile).Length / 1MB))
            return $true
        }
        Say-Warn "curl 失败 (exit $LASTEXITCODE)，改试 Invoke-WebRequest"
    }

    try {
        $p = @{ Uri = $Url; OutFile = $OutFile; UseBasicParsing = $true }
        if ($ProxyUrl) { $p['Proxy'] = $ProxyUrl }
        Invoke-WebRequest @p
        return $true
    } catch {
        Say-Warn "下载失败：$($_.Exception.Message)"
        return $false
    }
}

# ---------------------------------------------------------------- 定位工具
function Get-TesseractExe {
    foreach ($rel in @('tesseract.exe', 'Library\bin\tesseract.exe')) {
        $p = Join-Path $DestDir $rel
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $cmd = Get-Command tesseract.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-TessdataDir {
    param([string]$Exe)
    $cands = @(
        (Join-Path $DestDir 'tessdata'),
        (Join-Path $DestDir 'Library\share\tessdata')
    )
    if ($Exe) {
        $cands += (Join-Path (Split-Path -Parent $Exe) 'tessdata')
        $cands += (Join-Path (Split-Path -Parent (Split-Path -Parent $Exe)) 'share\tessdata')
    }
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    return $null
}

function Get-CondaExe {
    if ($env:CONDA_EXE -and (Test-Path -LiteralPath $env:CONDA_EXE)) { return $env:CONDA_EXE }
    $cmd = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in @(
        'F:\miniconda\Scripts\conda.exe',
        'F:\miniconda3\Scripts\conda.exe',
        (Join-Path $env:USERPROFILE 'miniconda3\Scripts\conda.exe'),
        (Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'),
        'C:\ProgramData\miniconda3\Scripts\conda.exe',
        'C:\ProgramData\anaconda3\Scripts\conda.exe'
    )) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- 安装方式
function Install-ByConda {
    $conda = Get-CondaExe
    if (-not $conda) {
        Say-Warn '未找到 conda（可用 -Method installer 或先安装 Miniconda 到 F 盘）'
        return $false
    }
    Say-Info "使用 conda：$conda"
    if (Test-Path -LiteralPath $DestDir) {
        Say-Info '清理旧的 tesseract 前缀'
        Remove-Item -LiteralPath $DestDir -Recurse -Force
    }
    # --override-channels：只用 conda-forge，避免 defaults 的商用条款与网络问题
    & $conda create -y -p $DestDir -c conda-forge --override-channels tesseract
    if ($LASTEXITCODE -ne 0) {
        Say-Warn "conda create 失败（退出码 $LASTEXITCODE）"
        return $false
    }
    return $true
}

function Install-ByInstaller {
    $exe = Join-Path $DlDir (Split-Path -Leaf $InstallerUrl)
    if (-not (Invoke-Download -Url $InstallerUrl -OutFile $exe)) {
        Say-Warn '安装包下载失败'
        return $false
    }
    if (Test-Path -LiteralPath $DestDir) { Remove-Item -LiteralPath $DestDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null

    # Inno Setup 静默参数：不注册系统项、不加 PATH、不装其它语言组件
    $args = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS=',
              "/DIR=`"$DestDir`"", '/COMPONENTS="main"')
    Say-Info '静默安装（需要管理员权限，可能弹出 UAC）'
    try {
        if (Test-Admin) {
            $p = Start-Process -FilePath $exe -ArgumentList $args -Wait -PassThru -NoNewWindow
        } else {
            $p = Start-Process -FilePath $exe -ArgumentList $args -Verb RunAs -Wait -PassThru
        }
    } catch {
        Say-Warn "安装未完成：$($_.Exception.Message)"
        return $false
    }
    Say-Info "安装程序退出码 $($p.ExitCode)"
    return ($null -ne (Get-TesseractExe))
}

# ---------------------------------------------------------------- 语言包
function Install-Language {
    param([string]$TessdataDir, [string]$Lang)
    $dst = Join-Path $TessdataDir "$Lang.traineddata"
    if ((Test-Path -LiteralPath $dst) -and -not $Force -and (Get-Item -LiteralPath $dst).Length -gt 0) {
        Say-Info "$Lang 已存在，跳过"
        return $true
    }
    $url = "https://github.com/tesseract-ocr/$Repo/raw/main/$Lang.traineddata"
    return (Invoke-Download -Url $url -OutFile $dst)
}

# ---------------------------------------------------------------- 主流程
$exe = Get-TesseractExe
$tessdata = if ($exe) { Get-TessdataDir -Exe $exe } else { $null }
$missing = @()
if ($tessdata) {
    foreach ($lang in $Langs) {
        if (-not (Test-Path -LiteralPath (Join-Path $tessdata "$lang.traineddata"))) { $missing += $lang }
    }
} else {
    $missing = $Langs
}

if ($exe -and -not $Force -and $missing.Count -eq 0) {
    Say-Ok "Tesseract 已就绪：$exe"
    Say-Ok "语言包齐全（$($Langs -join ', ')）"
} else {
    Say-Step '安装 Tesseract 可执行文件'
    $ok = $false
    if ($Method -eq 'conda' -or $Method -eq 'auto') { $ok = Install-ByConda }
    if (-not $ok -and ($Method -eq 'installer' -or $Method -eq 'auto')) { $ok = Install-ByInstaller }
    if (-not $ok) {
        Say-Warn 'Tesseract 安装失败。可手动安装后重跑本脚本，或参考 https://github.com/UB-Mannheim/tesseract/wiki'
        exit 1
    }

    $exe = Get-TesseractExe
    if (-not $exe) { Say-Warn "未找到 tesseract.exe（预期在 $DestDir）"; exit 1 }
    Say-Ok "tesseract.exe -> $exe"
    $tessdata = Get-TessdataDir -Exe $exe
    if (-not $tessdata) { Say-Warn '未找到 tessdata 目录'; exit 1 }
}

Say-Step "语言包（$Repo）"
$failed = @()
foreach ($lang in $Langs) {
    if (-not (Install-Language -TessdataDir $tessdata -Lang $lang)) { $failed += $lang }
}

Say-Step '自检'
$env:TESSDATA_PREFIX = $tessdata
$ver = & $exe --version 2>&1 | Select-Object -First 1
Say-Ok "版本：$ver"
$langs = & $exe --list-langs 2>&1 | Select-Object -Skip 1
Say-Ok "已安装语言：$($langs -join ', ')"
Say-Info "tessdata：$tessdata"

if ($failed.Count -gt 0) {
    Write-Host "`n以下语言包未下载成功：$($failed -join ', ')" -ForegroundColor Yellow
    exit 1
}
Write-Host "`nTesseract OCR 就绪，docpix 的 OCR 功能可用。" -ForegroundColor Green
