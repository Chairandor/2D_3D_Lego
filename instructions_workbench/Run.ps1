param(
    [ValidateSet('chicken','bunny','mpd','all')][string]$Model = 'all',
    [ValidateSet('instructions-first','full','legacy-full','plan','check','status','export-draft','prepare','verify-steps','source-plan')][string]$Mode = 'instructions-first',
    [string]$Config = (Join-Path $PSScriptRoot 'config\local.json'),
    [string]$InputModel = '',
    [string]$Title = '',
    [string]$PlanRun = '',
    [string]$Python = '',
    [switch]$Setup,
    [ValidateRange(0.001,1800)][double]$AnalysisSeconds = 1800
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) { throw "Config not found: $Config" }
    $settings = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Setup) {
        & (Join-Path $PSScriptRoot 'Setup.ps1')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    if (-not $Python) {
        $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $projectPython -PathType Leaf) { $Python = $projectPython }
        elseif ($settings.python -and (Test-Path -LiteralPath $settings.python -PathType Leaf)) { $Python = $settings.python }
        else { throw 'Python environment not found. Run the same command with -Setup (Python 3.12 must be installed).' }
    }
    if ($Mode -in @('full','plan','prepare','verify-steps')) {
        # The former suite has no OS lock: prevent parallel legacy/new planning.
        $legacy = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match '(build_instructions\.py|legolization_060_adapter\.py)' -and $_.CommandLine -notmatch '(--check|--help)' })
        if ($legacy.Count -gt 0) {
            $legacy | Select-Object ProcessId, CommandLine | Format-List
            throw 'A previous LEGO task is still running. Wait for it to finish before using this project; no process was stopped.'
        }
    }
    $arguments = @('-u', (Join-Path $PSScriptRoot 'run.py'), '--config', $Config, '--mode', $Mode, '--analysis-seconds', $AnalysisSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))
    if ($InputModel) { $arguments += @('--input', $InputModel) }
    else { $arguments += @('--model', $Model) }
    if ($Title) { $arguments += @('--title', $Title) }
    if ($PlanRun) { $arguments += @('--plan-run', $PlanRun) }
    & $Python @arguments
    $pipelineExitCode = $LASTEXITCODE
    if ($pipelineExitCode -eq 2) { Write-Host 'Outputs are saved. Stability findings remain: DRAFT_REVIEW_REQUIRED.' }
    exit $pipelineExitCode
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
