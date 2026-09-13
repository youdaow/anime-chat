# 跑测试
chcp.com 65001 > $null 2>&1
$env:PYTHONUTF8 = "1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .venv\Scripts\python.exe -m pytest -q @args
