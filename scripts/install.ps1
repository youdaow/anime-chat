# 一次装好：建 venv、装依赖、生成内置表情包与头像、跑自检。
# 用法： powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
chcp.com 65001 > $null 2>&1        # 自检输出是中文，别让 GBK 终端显示成乱码
$env:PYTHONUTF8 = "1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

if (-not (Have uv)) {
  if (-not (Have python)) { Write-Host "没找到 python，先装一个（https://www.python.org/downloads/，勾选 Add to PATH）" -ForegroundColor Red; exit 1 }
  Write-Host "没找到 uv，用 pip 装 uv ..." -ForegroundColor Yellow
  python -m pip install --user uv
  $py = (Get-Command python).Source
  $userScripts = Join-Path (Split-Path -Parent (Split-Path -Parent $py)) "Scripts"
  if (-not (Have uv)) { $env:PATH = "$userScripts;$env:PATH" }
}

Write-Host "→ 建虚拟环境 .venv (Python 3.12+)" -ForegroundColor Cyan
if (-not (Test-Path .venv)) { uv venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "→ 安装依赖" -ForegroundColor Cyan
uv pip install --python $py -e ".[dev]"

Write-Host "→ 生成内置表情包 / 头像" -ForegroundColor Cyan
& $py -m animechat.cli build-assets

Write-Host "→ 自检" -ForegroundColor Cyan
& $py -m animechat.cli doctor

Write-Host ""
Write-Host "装好了。下一步： .\scripts\start.ps1   然后浏览器打开 http://127.0.0.1:8899" -ForegroundColor Green
