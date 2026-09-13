# 停掉占用端口的 animechat 进程（默认 8899）。
param([int]$Port = 8899)
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $conns) { Write-Host "端口 $Port 上没有在跑的服务"; exit 0 }
foreach ($c in $conns) {
  $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
  if ($p) { Write-Host ("停止 {0} (pid {1})" -f $p.ProcessName, $p.Id); Stop-Process -Id $p.Id -Force }
}
