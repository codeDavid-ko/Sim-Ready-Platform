# start_all.ps1 - Launch Sim-Ready Platform: backend(8000) + frontend(3000) + Cloudflare quick tunnel.
#   Used by both the logon auto-start task and manual runs.
#   The external URL (*.trycloudflare.com) is a FREE EPHEMERAL tunnel: a new URL every start.
#   The issued URL is written to %USERPROFILE%\.algo-runner\tunnel_url.txt
$ErrorActionPreference = "SilentlyContinue"

$root  = Split-Path $PSScriptRoot -Parent
$back  = Join-Path $root "backend"
$front = Join-Path $root "frontend"
$uv    = "C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
$cf    = "C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
$pnpm  = "C:\Users\user\AppData\Roaming\npm\pnpm.cmd"
if (-not (Test-Path $pnpm)) { $pnpm = "C:\Program Files\nodejs\npm.cmd" }   # fallback
$stateDir = Join-Path $env:USERPROFILE ".algo-runner"
$logDir   = Join-Path $stateDir "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$urlFile  = Join-Path $stateDir "tunnel_url.txt"
$cfLog    = Join-Path $logDir   "cloudflared.log"

function Kill-By($pat) {
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like $pat } |
    ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
}

# avoid duplicates: clean existing instances
Kill-By '*algo_runner._serve*'
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
Kill-By '*next*dev*'
Start-Sleep -Seconds 1
Remove-Item $urlFile -Force -ErrorAction SilentlyContinue

# 1) backend (8000)
Start-Process -FilePath $uv -ArgumentList 'run','python','-m','algo_runner._serve' -WorkingDirectory $back -WindowStyle Hidden
# 2) frontend (3000)
Start-Process -FilePath $pnpm -ArgumentList 'dev' -WorkingDirectory $front -WindowStyle Hidden

# 3) wait backend health
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  try { if (Invoke-RestMethod "http://127.0.0.1:8000/api/status" -TimeoutSec 3) { $ok = $true; break } } catch {}
  Start-Sleep -Seconds 2
}
Write-Host ("[start_all] backend healthy = {0}" -f $ok)

# 4) wait frontend (3000)
for ($i = 0; $i -lt 60; $i++) {
  if (Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue) { break }
  Start-Sleep -Seconds 2
}

# 5) Cloudflare quick tunnel (expose frontend 3000; same-origin proxy covers the API)
if (Test-Path $cf) {
  Start-Process -FilePath $cf -ArgumentList 'tunnel','--url','http://localhost:3000','--no-autoupdate' -WindowStyle Hidden -RedirectStandardError $cfLog -RedirectStandardOutput (Join-Path $logDir "cloudflared.out")
  $url = $null
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $cfLog) {
      $m = Select-String -Path $cfLog -Pattern 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($m) { $url = $m.Matches[0].Value; break }
    }
  }
  if ($url) { Set-Content -Path $urlFile -Value $url -Encoding utf8; Write-Host ("[start_all] PUBLIC URL = {0}" -f $url) }
  else { Write-Host "[start_all] tunnel URL not issued yet (check $cfLog)" }
} else {
  Write-Host "[start_all] cloudflared not found - local only http://localhost:3000"
}
