# 启动本地聊天服务并打开浏览器。
# 用法： .\scripts\start.ps1 [-Port 8899] [-Reload]
param(
  [int]$Port = 8899,
  [switch]$Reload
)
$ErrorActionPreference = "Stop"
chcp.com 65001 > $null 2>&1
$env:PYTHONUTF8 = "1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "还没装环境，先跑 .\scripts\install.ps1" -ForegroundColor Red; exit 1 }

$url = "http://127.0.0.1:$Port"
Start-Process powershell -ArgumentList "-NoProfile","-Command","Start-Sleep -Seconds 3; Start-Process '$url'" -WindowStyle Hidden
Write-Host "启动中 → $url  （Ctrl+C 停止）" -ForegroundColor Cyan
if ($Reload) { & $py -m animechat.cli run --port $Port --reload }
else { & $py -m animechat.cli run --port $Port }
