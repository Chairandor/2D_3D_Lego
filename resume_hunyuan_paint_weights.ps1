$ErrorActionPreference = 'Stop'

$repo = 'https://huggingface.co/tencent/Hunyuan3D-2/resolve/9cd649ba6913f7a852e3286bad86bfa9a2d83dcf'
$root = 'E:\3D_TEST\models\weights\Hunyuan3D-2-paint'
$cache = Join-Path $root '.cache\huggingface\download'

# Hugging Face stores partials by content ETag, not by destination filename.
# Keep the mapping explicit so two files in one component directory can never
# be confused (for example vae/config.json and the VAE weight file).
$partialEtags = @{
    'hunyuan3d-delight-v2-0/unet/diffusion_pytorch_model.safetensors' = '0ce61d15a43d11ba19079ab8f24dfce78b876d3f5291470079ef64b17e08ca58'
    'hunyuan3d-paint-v2-0-turbo/unet/diffusion_pytorch_model.safetensors' = 'd6acffa4a22f4da61d87f446bfa83e7ac245481c1535fbf25b200fe4462d0b22'
    'hunyuan3d-paint-v2-0-turbo/image_encoder/model.safetensors' = 'ae616c24393dd1854372b0639e5541666f7521cbe219669255e865cb7f89466a'
    'hunyuan3d-paint-v2-0-turbo/text_encoder/pytorch_model.bin' = 'c3e254d7b61353497ea0be2c4013df4ea8f739ee88cffa0ba58cd085459ed565'
    'hunyuan3d-paint-v2-0-turbo/vae/diffusion_pytorch_model.bin' = '1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc'
}

$files = @(
    'hunyuan3d-delight-v2-0/unet/diffusion_pytorch_model.safetensors',
    'hunyuan3d-paint-v2-0-turbo/unet/diffusion_pytorch_model.safetensors',
    'hunyuan3d-paint-v2-0-turbo/image_encoder/model.safetensors',
    'hunyuan3d-paint-v2-0-turbo/text_encoder/pytorch_model.bin',
    'hunyuan3d-paint-v2-0-turbo/vae/config.json',
    'hunyuan3d-paint-v2-0-turbo/vae/diffusion_pytorch_model.bin'
)

foreach ($relativeUrl in $files) {
    $relativePath = $relativeUrl.Replace('/', '\')
    $destination = Join-Path $root $relativePath
    if (Test-Path -LiteralPath $destination) {
        Write-Host "Already complete: $relativeUrl"
        continue
    }

    $destinationDir = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    $cacheDir = Join-Path $cache (Split-Path -Parent $relativePath)
    $etag = $partialEtags[$relativeUrl]
    $existing = if ($null -ne $etag) {
        Get-ChildItem -LiteralPath $cacheDir -Filter "*.$etag.incomplete" -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
    } else { $null }
    $partial = if ($null -ne $existing) { $existing.FullName } else { "$destination.part" }
    $startBytes = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0 }
    Write-Host "Downloading $relativeUrl (resuming at $startBytes bytes)"

    & curl.exe --fail --location --continue-at - --retry 100 --retry-all-errors `
        --retry-delay 2 --connect-timeout 30 --speed-time 60 --speed-limit 1024 `
        --output $partial "$repo/$relativeUrl"
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed for $relativeUrl with exit code $LASTEXITCODE"
    }
    Move-Item -LiteralPath $partial -Destination $destination -Force
    Write-Host "Complete: $relativeUrl ($((Get-Item -LiteralPath $destination).Length) bytes)"
}

Write-Host 'All large Paint files are ready.'
