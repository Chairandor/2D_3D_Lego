$ErrorActionPreference = "Stop"

$root = "E:\3D_TEST\models\weights\Paint3D"

$files = @(
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "model_index.json", "$root\sd15\model_index.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "unet/config.json", "$root\sd15\unet\config.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "unet/diffusion_pytorch_model.fp16.safetensors", "$root\sd15\unet\diffusion_pytorch_model.fp16.safetensors"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "vae/config.json", "$root\sd15\vae\config.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "vae/diffusion_pytorch_model.fp16.safetensors", "$root\sd15\vae\diffusion_pytorch_model.fp16.safetensors"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "text_encoder/config.json", "$root\sd15\text_encoder\config.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "text_encoder/model.fp16.safetensors", "$root\sd15\text_encoder\model.fp16.safetensors"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "tokenizer/merges.txt", "$root\sd15\tokenizer\merges.txt"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "tokenizer/special_tokens_map.json", "$root\sd15\tokenizer\special_tokens_map.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "tokenizer/tokenizer_config.json", "$root\sd15\tokenizer\tokenizer_config.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "tokenizer/vocab.json", "$root\sd15\tokenizer\vocab.json"),
    @("stable-diffusion-v1-5/stable-diffusion-v1-5", "scheduler/scheduler_config.json", "$root\sd15\scheduler\scheduler_config.json"),
    @("lllyasviel/control_v11f1p_sd15_depth", "config.json", "$root\controlnet-depth\config.json"),
    @("lllyasviel/control_v11f1p_sd15_depth", "diffusion_pytorch_model.fp16.safetensors", "$root\controlnet-depth\diffusion_pytorch_model.fp16.safetensors"),
    @("lllyasviel/control_v11p_sd15_inpaint", "config.json", "$root\controlnet-inpaint\config.json"),
    @("lllyasviel/control_v11p_sd15_inpaint", "diffusion_pytorch_model.fp16.safetensors", "$root\controlnet-inpaint\diffusion_pytorch_model.fp16.safetensors"),
    @("h94/IP-Adapter", "models/ip-adapter_sd15.safetensors", "$root\ip-adapter\models\ip-adapter_sd15.safetensors"),
    @("h94/IP-Adapter", "models/image_encoder/config.json", "$root\ip-adapter\models\image_encoder\config.json"),
    @("h94/IP-Adapter", "models/image_encoder/model.safetensors", "$root\ip-adapter\models\image_encoder\model.safetensors")
)

foreach ($entry in $files) {
    $repo = $entry[0]
    $path = $entry[1]
    $destination = $entry[2]
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    if (Test-Path -LiteralPath $destination) {
        Write-Host "SKIP $destination"
        continue
    }

    $partial = "$destination.part"
    $escapedPath = ($path -split "/" | ForEach-Object { [uri]::EscapeDataString($_) }) -join "/"
    $url = "https://huggingface.co/$repo/resolve/main/${escapedPath}?download=true"
    Write-Host "GET  $repo/$path"
    & curl.exe --fail --location --continue-at - --retry 100 --retry-all-errors --retry-delay 2 --connect-timeout 30 --speed-time 90 --speed-limit 1024 --output $partial $url
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed with exit code $LASTEXITCODE for $repo/$path"
    }
    Move-Item -LiteralPath $partial -Destination $destination
}

Write-Host "Paint3D minimal stage-1 weights are complete."
