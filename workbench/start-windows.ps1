$ErrorActionPreference = 'Stop'
$WorkbenchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendRoot = Join-Path $WorkbenchRoot 'backend'
$FrontendRoot = Join-Path $WorkbenchRoot 'frontend'
$PythonExe = Join-Path $BackendRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw '请先运行 setup-windows.ps1'
}

Start-Process -FilePath $PythonExe -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $BackendRoot -WindowStyle Hidden
Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory $FrontendRoot -WindowStyle Hidden
Write-Host '工作台正在启动：http://127.0.0.1:5173'

