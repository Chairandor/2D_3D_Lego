param(
    [int]$Port = 7865,
    [string]$PythonExe = '',
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:$Port/?mode=ar"
$existing = $null
try { $existing = Invoke-RestMethod "http://127.0.0.1:$Port/api/ar/bootstrap" -TimeoutSec 2 } catch {}
if ($null -ne $existing -and $existing.PSObject.Properties.Name -contains 'available') {
    Write-Host "工作台已在运行：$url"
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
}
if (-not $PythonExe) {
    if ($env:ARUCO_PYTHON) { $PythonExe = $env:ARUCO_PYTHON }
    else { $PythonExe = Join-Path (Split-Path -Parent $projectRoot) 'aruco_lego\.venv\Scripts\python.exe' }
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw '未找到本机 AR Python 环境。用 -PythonExe 指定可运行工作台的 Python，或先配置 aruco_lego/.venv。'
}
$uiArgs = @('-u', (Join-Path $projectRoot 'pipeline_ui.py'), '--port', "$Port", '--mode', 'ar')
if ($NoBrowser) { $uiArgs += '--no-browser' }
Write-Host "ArUco 页面已合并在原工作台：$url；Ctrl+C 退出并释放相机。"
& $PythonExe @uiArgs
if ($LASTEXITCODE -ne 0) { throw '工作台退出失败；请检查上方错误和端口占用。' }
