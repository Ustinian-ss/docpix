# docpix 环境隔离脚本
# 目的：把一切临时文件 / 缓存 / 构建产物都强制落在项目目录内（F 盘），
#       绝不写入 C:\Users\...\AppData\Local\Temp 或任何 C 盘缓存目录。
# 用法：在其它脚本里  . "$PSScriptRoot\env.ps1"

$DocpixRoot = Split-Path -Parent $PSScriptRoot          # ...\docpix

$env:DOCPIX_ROOT  = $DocpixRoot
$env:DOCPIX_BIN   = Join-Path $DocpixRoot 'bin'
$env:DOCPIX_VAR   = Join-Path $DocpixRoot 'var'
$env:DOCPIX_CACHE = Join-Path $DocpixRoot '.cache'

$dirs = @(
    $env:DOCPIX_BIN,
    $env:DOCPIX_VAR,
    (Join-Path $env:DOCPIX_VAR 'in'),
    (Join-Path $env:DOCPIX_VAR 'out'),
    (Join-Path $env:DOCPIX_VAR 'jobs'),
    $env:DOCPIX_CACHE,
    (Join-Path $env:DOCPIX_CACHE 'downloads'),
    (Join-Path $env:DOCPIX_CACHE 'pip'),
    (Join-Path $env:DOCPIX_CACHE 'pycache'),
    (Join-Path $env:DOCPIX_CACHE 'conda-pkgs'),
    (Join-Path $env:DOCPIX_CACHE 'huggingface'),
    (Join-Path $DocpixRoot '.tmp'),
    (Join-Path $DocpixRoot '.tmp\pip'),
    (Join-Path $DocpixRoot '.tmp\build'),
    (Join-Path $DocpixRoot '.tmp\soffice')
)
foreach ($d in $dirs) { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null } }

# ---- 临时目录：全量重定向到 F 盘 ----
$env:TEMP = Join-Path $DocpixRoot '.tmp'
$env:TMP  = $env:TEMP

# ---- Python 侧缓存 ----
$env:PIP_CACHE_DIR        = Join-Path $env:DOCPIX_CACHE 'pip'
$env:PYTHONPYCACHEPREFIX  = Join-Path $env:DOCPIX_CACHE 'pycache'
$env:CONDA_PKGS_DIRS      = Join-Path $env:DOCPIX_CACHE 'conda-pkgs'
$env:HF_HOME              = Join-Path $env:DOCPIX_CACHE 'huggingface'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

# ---- 让 LibreOffice 的用户配置也落在 F 盘（否则会写 %APPDATA%） ----
$env:DOCPIX_SOFFICE_PROFILE = Join-Path $DocpixRoot '.tmp\soffice'
$env:USER_INSTALL_ROOT      = $env:DOCPIX_SOFFICE_PROFILE

function Get-DocpixPython {
    $venv = Join-Path $env:DOCPIX_ROOT '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { return $venv }
    return 'F:\miniconda\python.exe'
}

function Get-FreeSpaceGB([string]$Path) {
    $qualifier = (Resolve-Path -LiteralPath $Path).Path.Substring(0,2)
    (Get-Volume -DriveLetter $qualifier[0]).SizeRemaining / 1GB
}
