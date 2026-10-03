<#
  docpix 外部引擎获取脚本
  ------------------------------------------------------------------
  原则：
    1. 只从官方发布渠道下载（合规），不使用任何第三方转载/破解包。
    2. 全部下载并解压到 F 盘项目目录内，不写 C 盘。
    3. 需要代理时自动探测 Clash Verge 的混合端口，并按需拉起 GUI。

  用法：
    pwsh -File tools\fetch_engines.ps1                 # 下载缺失的引擎
    pwsh -File tools\fetch_engines.ps1 -Force          # 全部重新下载
    pwsh -File tools\fetch_engines.ps1 -Only qpdf,pandoc
    pwsh -File tools\fetch_engines.ps1 -Proxy on -ProxyUrl http://127.0.0.1:7897
#>
[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [switch]$Force,
    # msiexec /a 解包 LibreOffice 需要管理员令牌；默认在需要时弹一次 UAC。
    # 传 -NoElevate 则只提示、不弹窗（适合无人值守环境）。
    [switch]$NoElevate,
    [ValidateSet('auto', 'off', 'on')][string]$Proxy = 'auto',
    [string]$ProxyUrl = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'env.ps1')

$DlDir  = Join-Path $env:DOCPIX_CACHE 'downloads'
$BinDir = $env:DOCPIX_BIN

function Say-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Say-Ok($m)   { Write-Host "    [OK]  $m" -ForegroundColor Green }
function Say-Warn($m) { Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Say-Info($m) { Write-Host "    $m" -ForegroundColor DarkGray }

Write-Host "docpix 引擎获取" -ForegroundColor White
Say-Info "项目根目录 : $env:DOCPIX_ROOT"
Say-Info "下载缓存   : $DlDir"
Say-Info "安装目标   : $BinDir"
Say-Info ("F 盘剩余   : {0:N1} GB" -f (Get-FreeSpaceGB 'F:\'))

# ---------------------------------------------------------------- 代理探测
function Get-ClashVergeMixedPort {
    $cfg = Join-Path $env:APPDATA 'io.github.clash-verge-rev.clash-verge-rev\verge.yaml'
    if (Test-Path -LiteralPath $cfg) {
        $m = Select-String -LiteralPath $cfg -Pattern '^\s*verge_mixed_port:\s*(\d+)' | Select-Object -First 1
        if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    }
    return 7897
}

function Test-ProxyPort([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $Port); $c.Close(); return $true
    } catch { return $false }
}

function Start-ClashVergeIfFound {
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Clash Verge\Clash Verge.exe",
        "$env:LOCALAPPDATA\Programs\clash-verge\Clash Verge.exe",
        'C:\Program Files\Clash Verge\Clash Verge.exe',
        "$env:ProgramFiles\Clash Verge\Clash Verge.exe"
    )
    foreach ($c in $cands) {
        if (Test-Path -LiteralPath $c) {
            Say-Warn "代理未监听，尝试启动 Clash Verge：$c"
            Start-Process -FilePath $c | Out-Null
            for ($i = 0; $i -lt 30; $i++) {
                Start-Sleep -Seconds 1
                if (Test-ProxyPort $Port) { return $true }
            }
            return $false
        }
    }
    Say-Warn '未找到 Clash Verge 可执行文件，跳过自动启动'
    return $false
}

$script:CurlProxyArg = @()
if ($Proxy -ne 'off') {
    if ($ProxyUrl) {
        $script:CurlProxyArg = @('-x', $ProxyUrl)
        Say-Ok "使用指定代理 $ProxyUrl"
    } else {
        $port = Get-ClashVergeMixedPort
        $live = Test-ProxyPort $port
        if (-not $live -and $Proxy -eq 'auto') { $live = Start-ClashVergeIfFound }
        if ($live) {
            $ProxyUrl = "http://127.0.0.1:$port"
            $script:CurlProxyArg = @('-x', $ProxyUrl)
            Say-Ok "使用 Clash Verge 代理 $ProxyUrl"
        } else {
            Say-Info '代理不可用，直连下载'
        }
    }
} else {
    Say-Info '已禁用代理，直连下载'
}

# ---------------------------------------------------------------- 下载实现
function Invoke-Download {
    param([string]$Url, [string]$OutFile, [switch]$NoResume)

    if ((Test-Path -LiteralPath $OutFile) -and -not $Force -and -not $NoResume) {
        $sz = (Get-Item -LiteralPath $OutFile).Length
        if ($sz -gt 0) { Say-Info "已存在，跳过：$(Split-Path -Leaf $OutFile) ($([math]::Round($sz/1MB,1)) MB)"; return $true }
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
        Say-Ok ("$leaf  {0:N1} MB" -f ((Get-Item -LiteralPath $OutFile).Length / 1MB))
        return $true
    } catch {
        Say-Warn "下载失败：$($_.Exception.Message)"
        return $false
    }
}

function Expand-ZipTo {
    param([string]$Zip, [string]$Dest)
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    Expand-Archive -LiteralPath $Zip -DestinationPath $Dest -Force
}

# ------------------------------------------------------- 管理员权限（解包 MSI）
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# msiexec 会往 C:\Windows\Installer 写回滚脚本，非管理员必然 1603，
# 因此这里按需弹一次 UAC（PowerShell 5.1 同样支持 Start-Process -Verb RunAs）。
function Invoke-MsiexecAdmin {
    param([string]$Msi, [string]$Dest, [string]$Log)

    $msiexec = Join-Path $env:SystemRoot 'System32\msiexec.exe'
    $msiArgs = @('/a', "`"$Msi`"", '/qn', "TARGETDIR=`"$Dest`"", '/L*v', "`"$Log`"")

    if (Test-Admin) {
        $p = Start-Process -FilePath $msiexec -ArgumentList $msiArgs -Wait -PassThru -NoNewWindow
        return $p.ExitCode
    }
    if ($NoElevate) {
        Say-Warn '当前不是管理员，且已指定 -NoElevate；请用「以管理员身份运行」的 PowerShell 重新执行本脚本。'
        return 1603
    }

    Say-Warn 'msiexec 解包需要管理员权限，正在弹出 UAC 授权窗口（请点「是」）…'
    try {
        $p = Start-Process -FilePath $msiexec -ArgumentList $msiArgs -Verb RunAs -Wait -PassThru
    } catch {
        Say-Warn "未能获得管理员授权：$($_.Exception.Message)"
        return 1603
    }
    return $p.ExitCode
}

# ---------------------------------------------------------------- 引擎定义
$Engines = [ordered]@{

    qpdf = @{
        Title = 'qpdf (Apache-2.0) —— PDF 无损结构处理'
        Check = { Test-Path -LiteralPath (Join-Path $BinDir 'qpdf\bin\qpdf.exe') }
        Run   = {
            $ver = '12.4.2'
            $zip = Join-Path $DlDir "qpdf-$ver-msvc64.zip"
            $url = "https://github.com/qpdf/qpdf/releases/download/v$ver/qpdf-$ver-msvc64.zip"
            if (-not (Invoke-Download -Url $url -OutFile $zip)) { return $false }
            $tmp = Join-Path $DlDir 'qpdf-x'
            Expand-ZipTo -Zip $zip -Dest $tmp
            $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
            $dest = Join-Path $BinDir 'qpdf'
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
            Move-Item -LiteralPath $inner.FullName -Destination $dest
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            return (Test-Path -LiteralPath (Join-Path $dest 'bin\qpdf.exe'))
        }
    }

    pandoc = @{
        Title = 'Pandoc (GPL-2.0+) —— 标记语言互转，独立进程调用，不污染本项目 MIT 许可'
        Check = { Test-Path -LiteralPath (Join-Path $BinDir 'pandoc\pandoc.exe') }
        Run   = {
            $api = 'https://api.github.com/repos/jgm/pandoc/releases/latest'
            Say-Info '查询 Pandoc 最新版本'
            $rel = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'docpix' }
            $asset = $rel.assets | Where-Object { $_.name -match 'windows-x86_64\.zip$' } | Select-Object -First 1
            if (-not $asset) { Say-Warn '未能定位 Windows 版 Pandoc 资源'; return $false }
            $zip = Join-Path $DlDir $asset.name
            if (-not (Invoke-Download -Url $asset.browser_download_url -OutFile $zip)) { return $false }
            $dest = Join-Path $BinDir 'pandoc'
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
            Expand-ZipTo -Zip $zip -Dest $dest
            $exe = Get-ChildItem -LiteralPath $dest -Recurse -Filter 'pandoc.exe' | Select-Object -First 1
            if ($exe) {
                if ($exe.DirectoryName -ne $dest) {
                    $flat = Join-Path $BinDir 'pandoc-flat'
                    if (Test-Path -LiteralPath $flat) { Remove-Item -LiteralPath $flat -Recurse -Force }
                    Move-Item -LiteralPath $exe.DirectoryName -Destination $flat
                    Remove-Item -LiteralPath $dest -Recurse -Force
                    Move-Item -LiteralPath $flat -Destination $dest
                }
                return (Test-Path -LiteralPath (Join-Path $dest 'pandoc.exe'))
            }
            return $false
        }
    }

    libreoffice = @{
        Title = 'LibreOffice (MPL-2.0) —— Office 文档高保真互转核心'
        Check = { Test-Path -LiteralPath (Join-Path $BinDir 'libreoffice\program\soffice.exe') }
        Run   = {
            $ver = '26.8.0'
            $msi = Join-Path $DlDir "LibreOffice_${ver}_Win_x86-64.msi"
            $url = "https://download.documentfoundation.org/libreoffice/stable/$ver/win/x86_64/LibreOffice_${ver}_Win_x86-64.msi"
            if (-not (Invoke-Download -Url $url -OutFile $msi)) { return $false }

            $dest = Join-Path $BinDir 'libreoffice'
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $dest | Out-Null

            Say-Info '管理员模式解包（不写 C 盘、不注册系统项）'
            $log = Join-Path $DlDir 'libreoffice-extract.log'
            $code = Invoke-MsiexecAdmin -Msi $msi -Dest $dest -Log $log
            Say-Info "msiexec /a 退出码 $code"

            $soffice = Get-ChildItem -LiteralPath $dest -Recurse -Filter 'soffice.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($soffice) {
                $program = $soffice.DirectoryName
                if ($program -ne (Join-Path $dest 'program')) {
                    $flat = Join-Path $BinDir 'lo-flat'
                    if (Test-Path -LiteralPath $flat) { Remove-Item -LiteralPath $flat -Recurse -Force }
                    Move-Item -LiteralPath (Split-Path -Parent $program) -Destination $flat
                    Remove-Item -LiteralPath $dest -Recurse -Force
                    Move-Item -LiteralPath $flat -Destination $dest
                }
                return (Test-Path -LiteralPath (Join-Path $dest 'program\soffice.exe'))
            }
            Say-Warn "解包未找到 soffice.exe，详见 $log"
            return $false
        }
    }
}

# ---------------------------------------------------------------- 主流程
$targets = if ($Only.Count -gt 0) { $Only } else { $Engines.Keys }
$failed  = @()

foreach ($name in $targets) {
    if (-not $Engines.Contains($name)) { Say-Warn "未知引擎：$name"; continue }
    $e = $Engines[$name]
    Say-Step "$name — $($e.Title)"

    if (-not $Force -and (& $e.Check)) { Say-Ok '已就位，跳过'; continue }

    try {
        $ok = & $e.Run
        if ($ok) { Say-Ok "$name 部署完成" } else { $failed += $name; Say-Warn "$name 部署失败" }
    } catch {
        $failed += $name
        Say-Warn "$name 异常：$($_.Exception.Message)"
    }
}

Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host '全部引擎就绪。' -ForegroundColor Green
} else {
    Write-Host "以下引擎未就绪：$($failed -join ', ')" -ForegroundColor Yellow
    exit 1
}
