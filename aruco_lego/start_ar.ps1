param(
    [ValidateSet('chicken', 'jackjack', 'demo')][string]$Model = 'chicken',
    [int]$Camera = 0,
    [double]$MarkerSize = 0.06,
    [double]$FitWidth = 0.09,
    [int]$MarkerId = 0,
    [string]$Calibration = '',
    [string]$StudioColors = ''
)
$ErrorActionPreference = 'Stop'
$arRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $arRoot
$pythonExe = Join-Path $arRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw '缺少 AR 环境，请按 aruco_lego/README.md 安装依赖。'
}
$modelPath = switch ($Model) {
    'chicken' { Join-Path $projectRoot 'chicken.ldr' }
    'jackjack' { Join-Path $projectRoot 'jackjack失败版.ldr' }
    'demo' { '' }
}
$runDir = Join-Path $arRoot ('runs\live_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
$arArgs = @('-u', (Join-Path $arRoot 'aruco_lego_ar.py'), '--camera', "$Camera",
    '--marker-size', "$MarkerSize", '--fit-width', "$FitWidth", '--marker-id', "$MarkerId",
    '--log-dir', $runDir)
if ($modelPath) { $arArgs += @('--model', $modelPath) }
if ($Model -eq 'jackjack' -and -not $StudioColors) {
    $shortcutPath = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Studio 2.0.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        $studioShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
        $candidate = Join-Path (Split-Path -Parent $studioShortcut.TargetPath) 'data\StudioColorDefinition.txt'
        if (Test-Path -LiteralPath $candidate) { $StudioColors = $candidate }
    }
}
if ($StudioColors) { $arArgs += @('--studio-colors', $StudioColors) }
if ($Calibration) { $arArgs += @('--calibration', $Calibration) }
Write-Host "模型：$Model；码边长：$MarkerSize 米；状态记录：$runDir"
& $pythonExe @arArgs
if ($LASTEXITCODE -ne 0) { throw "AR 退出失败，请检查上方错误与 $runDir\status.json。" }
