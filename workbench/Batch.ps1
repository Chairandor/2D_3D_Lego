param(
    [string]$Manifest = (Join-Path $PSScriptRoot 'config\batch_manifest.json'),
    [string]$Config = (Join-Path $PSScriptRoot 'config\local.json'),
    [string]$State = '',
    [string]$Python = '',
    [string]$QaPython = '',
    [int]$MaxModelMinutes = 30,
    [switch]$Setup,
    [switch]$InventoryOnly
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    if ($Setup) { & (Join-Path $PSScriptRoot 'Setup.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
    $settings = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $Python) {
        $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $projectPython) { $Python = $projectPython }
        elseif ($settings.python -and (Test-Path -LiteralPath $settings.python)) { $Python = $settings.python }
        else { throw 'Python not found; use -Setup with Python 3.12 installed.' }
    }
    if (-not $QaPython) {
        if ($settings.qa_python -and (Test-Path -LiteralPath $settings.qa_python)) { $QaPython = $settings.qa_python }
        else { $QaPython = $Python }
    }
    $arguments = @('-u',(Join-Path $PSScriptRoot 'batch.py'),'--manifest',$Manifest,'--config',$Config,'--qa-python',$QaPython)
    $arguments += @('--max-model-minutes',[string]$MaxModelMinutes)
    if ($State) { $arguments += @('--state',$State) }
    if ($InventoryOnly) { $arguments += '--inventory-only' }
    & $Python @arguments
    exit $LASTEXITCODE
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
