# multidraw — 启动 Web UI（Windows）
$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
Set-Location $ROOT

$VENV_PY = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path $VENV_PY)) {
    Write-Host "✗ 未找到虚拟环境。请先跑 .\install.ps1" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "✗ 未找到 .env 文件。请从 .env.example 复制并填入 ZIMO_API_KEY" -ForegroundColor Red
    exit 1
}

Write-Host "[multidraw] starting web UI on http://127.0.0.1:8765" -ForegroundColor Green
Write-Host "  Ctrl+C 停止" -ForegroundColor Yellow
& $VENV_PY -m multidraw.cli serve
