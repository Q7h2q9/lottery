# multidraw — Windows 一键安装脚本（PowerShell）
#
# 前置要求：
#   1. Python 3.10+ 已装并加进 PATH（python --version 能跑）
#   2. Node.js 20+ 已装（node --version 能跑）
#   3. Git 已装（git --version 能跑）
#
# 用法（PowerShell 里）：
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
#   .\install.ps1
#
# 装完后：
#   1. 编辑 .env 填 ZIMO_API_KEY
#   2. 编辑 ~/.pi/agent/models.json 配 provider（首次跑 pi 会自动创建）
#   3. .\start.ps1 启动 Web UI

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
Set-Location $ROOT

Write-Host "[multidraw] checking prerequisites..." -ForegroundColor Cyan

# Python
try { $py = (& python --version) 2>&1; Write-Host "  Python: $py" } catch {
    Write-Host "  ✗ Python not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# Node
try { $nv = (& node --version) 2>&1; Write-Host "  Node:   $nv" } catch {
    Write-Host "  ✗ Node.js not found. Install from https://nodejs.org" -ForegroundColor Red
    exit 1
}

# Git
try { $gv = (& git --version) 2>&1; Write-Host "  Git:    $gv" } catch {
    Write-Host "  ✗ Git not found. Install from https://git-scm.com" -ForegroundColor Red
    exit 1
}

# --- 1. Python venv + 依赖 ---
$VENV = Join-Path $ROOT ".venv"
if (-not (Test-Path $VENV)) {
    Write-Host "[multidraw] creating venv at $VENV" -ForegroundColor Cyan
    & python -m venv $VENV
}

$VENV_PY = Join-Path $VENV "Scripts\python.exe"
$VENV_PIP = Join-Path $VENV "Scripts\pip.exe"

Write-Host "[multidraw] upgrading pip + wheel" -ForegroundColor Cyan
& $VENV_PY -m pip install --upgrade pip wheel | Out-Null

# --- 2. agentflow 源码 + editable 安装 ---
$AGENTFLOW_REPO = if ($env:AGENTFLOW_REPO) { $env:AGENTFLOW_REPO } else { "https://github.com/berabuddies/agentflow.git" }
$AGENTFLOW_SRC = Join-Path $ROOT ".deps\agentflow"

if (-not (Test-Path (Join-Path $AGENTFLOW_SRC ".git"))) {
    Write-Host "[multidraw] cloning agentflow into $AGENTFLOW_SRC" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $AGENTFLOW_SRC) | Out-Null
    & git clone --depth 1 $AGENTFLOW_REPO $AGENTFLOW_SRC
}

Write-Host "[multidraw] installing agentflow (editable)" -ForegroundColor Cyan
& $VENV_PIP install -e $AGENTFLOW_SRC

Write-Host "[multidraw] installing multidraw (editable + dev)" -ForegroundColor Cyan
& $VENV_PIP install -e "$ROOT[dev]"

# --- 3. pi CLI（Node 全局包）---
Write-Host "[multidraw] installing pi CLI globally (npm)" -ForegroundColor Cyan
try {
    & npm install -g @pidev/pi 2>&1 | Out-Null
} catch {
    Write-Host "  (npm package name 可能不同，下面会跳过；可以手动 npm i -g pi)" -ForegroundColor Yellow
}

# --- 4. .env 模板 ---
$ENV_FILE = Join-Path $ROOT ".env"
if (-not (Test-Path $ENV_FILE)) {
    Write-Host "[multidraw] creating .env from .env.example" -ForegroundColor Cyan
    Copy-Item ".env.example" $ENV_FILE
    Write-Host "  → 请编辑 $ENV_FILE 填入 ZIMO_API_KEY" -ForegroundColor Yellow
}

# --- 5. pi 配置目录 ---
$PI_HOME = Join-Path $env:USERPROFILE ".pi\agent"
if (-not (Test-Path $PI_HOME)) {
    New-Item -ItemType Directory -Force -Path $PI_HOME | Out-Null
}
$MODELS_JSON = Join-Path $PI_HOME "models.json"
if (-not (Test-Path $MODELS_JSON)) {
    Write-Host "[multidraw] writing default $MODELS_JSON" -ForegroundColor Cyan
    @'
{
  "providers": {
    "zimo": {
      "baseUrl": "https://llm.zimo.click/",
      "api": "openai-responses",
      "apiKey": "ZIMO_API_KEY",
      "models": [
        { "id": "gpt-5.4", "reasoning": true, "contextWindow": 1000000 }
      ]
    }
  }
}
'@ | Out-File -FilePath $MODELS_JSON -Encoding utf8 -NoNewline
}

# --- 6. 跑测试做 smoke check ---
Write-Host ""
Write-Host "[multidraw] running tests..." -ForegroundColor Cyan
& $VENV_PY -m pytest -q tests/

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host "  Install complete!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  1. 编辑 .env 填入 ZIMO_API_KEY"
Write-Host "  2. 启动 Web UI:  .\start.ps1"
Write-Host "  3. 浏览器访问:    http://127.0.0.1:8765"
Write-Host ""
