$ErrorActionPreference = 'Stop'
$WorkbenchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendRoot = Join-Path $WorkbenchRoot 'backend'
$FrontendRoot = Join-Path $WorkbenchRoot 'frontend'

python -m venv (Join-Path $BackendRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Python 虚拟环境创建失败' }
& (Join-Path $BackendRoot '.venv\Scripts\python.exe') -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip 更新失败' }
& (Join-Path $BackendRoot '.venv\Scripts\python.exe') -m pip install -r (Join-Path $BackendRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败' }
Push-Location $FrontendRoot
try { npm ci; if ($LASTEXITCODE -ne 0) { throw '前端锁定依赖安装失败' } } finally { Pop-Location }
Write-Host '依赖已安装。运行 .\start-windows.ps1 启动。'
