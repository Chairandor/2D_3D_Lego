# Installs ONLY into this project folder; never changes the original venv.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $basePython = $null
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ($launcher) {
            & $launcher.Source -3.12 -m venv (Join-Path $PSScriptRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is unavailable. Install it, then retry.' }
        } else {
            $basePython = Get-Command python -ErrorAction SilentlyContinue
            if (-not $basePython) { throw 'Install Python 3.12 first.' }
            & $basePython.Source -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
            & $basePython.Source -m venv (Join-Path $PSScriptRoot '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
        }
    }
    # Place pip cache and temporary build files on the project's drive.
    $oldPipCache = $env:PIP_CACHE_DIR
    $oldTemp = $env:TEMP
    $oldTmp = $env:TMP
    $buildTemp = Join-Path $PSScriptRoot '.setup_temp'
    New-Item -ItemType Directory -Path $buildTemp -Force | Out-Null
    $env:PIP_CACHE_DIR = Join-Path $buildTemp 'pip_cache'
    $env:TEMP = $buildTemp
    $env:TMP = $buildTemp
    try {
        $dependencyFile = Join-Path $PSScriptRoot 'requirements-lock.txt'
        if (-not (Test-Path -LiteralPath $dependencyFile)) { $dependencyFile = Join-Path $PSScriptRoot 'requirements.txt' }
        & $venvPython -m pip install -r $dependencyFile
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Retry when network is available.' }
        & $venvPython -m pip install --no-deps (Join-Path $PSScriptRoot 'vendor\legolization060')
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the supplied legolization 0.6.0 source.' }
    } finally {
        $env:PIP_CACHE_DIR = $oldPipCache
        $env:TEMP = $oldTemp
        $env:TMP = $oldTmp
    }
    Write-Host "Environment ready: $venvPython"
    $global:LASTEXITCODE = 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    $global:LASTEXITCODE = 1
}
