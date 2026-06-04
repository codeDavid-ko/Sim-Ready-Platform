# 백엔드(8000)와 프런트(3000)를 각각 새 창에서 띄운다.
$root = Split-Path $PSScriptRoot -Parent
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$root\backend'; uv run python -m algo_runner._serve"
Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$root\frontend'; pnpm dev"
Write-Host "백엔드 http://localhost:8000  프런트 http://localhost:3000"
