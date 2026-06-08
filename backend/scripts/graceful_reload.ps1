# graceful_reload.ps1 - restart backend without killing running jobs (near zero-downtime).
#   1) drain ON (reject new jobs) 2) wait for running jobs to finish 3) restart backend
#   4) wait healthy 5) drain OFF. Frontend (3000) stays up, so the site stays alive.
# Usage: powershell -ExecutionPolicy Bypass -File graceful_reload.ps1   (after editing backend code)
$ErrorActionPreference = "SilentlyContinue"
$drain = Join-Path $env:USERPROFILE ".algo-runner\DRAINING"
$uv    = "C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
$dir   = "C:\Users\user\Desktop\Claude\GIT\Sim-Ready-Platform\backend"
$base  = "http://127.0.0.1:8000"
$maxWaitSec = 900

New-Item -ItemType Directory -Force (Split-Path $drain) | Out-Null
Set-Content -Path $drain -Value "1"        # drain ON
Write-Host "[reload] drain ON - waiting for running jobs to finish..."

$waited = 0
while ($waited -lt $maxWaitSec) {
  $running = $null
  try { $s = Invoke-RestMethod "$base/api/status" -TimeoutSec 5; $running = [int]$s.jobs_running } catch { Write-Host "[reload] backend not responding - restarting now"; break }
  if ($running -le 0) { Write-Host "[reload] running jobs = 0 - restarting"; break }
  Write-Host ("[reload] running jobs = {0} ... waiting" -f $running)
  Start-Sleep -Seconds 3
  $waited += 3
}

# restart backend (drain file kept ON so the new process also rejects new jobs while booting)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*algo_runner._serve*' -and $_.Name -eq 'python.exe' } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
Start-Sleep -Seconds 2
Start-Process -FilePath $uv -ArgumentList 'run','python','-m','algo_runner._serve' -WorkingDirectory $dir -WindowStyle Hidden

$up = $false
for ($i = 0; $i -lt 45; $i++) {
  try { Invoke-RestMethod "$base/api/status" -TimeoutSec 3 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 }
}
Remove-Item $drain -Force -ErrorAction SilentlyContinue   # drain OFF
if ($up) { Write-Host "[reload] DONE - backend healthy, drain OFF" } else { Write-Host "[reload] WARN - backend not responding (check manually), drain OFF" }
