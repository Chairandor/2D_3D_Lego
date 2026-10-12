"""Download only the official Florence-2 files needed for an isolated trial."""

from pathlib import Path

from huggingface_hub import snapshot_download


destination = Path(__file__).resolve().parent / "florence_models" / "Florence-2-large-ft"
snapshot_download(
    repo_id="microsoft/Florence-2-large-ft",
    local_dir=destination,
    cache_dir=Path(__file__).resolve().parent / "florence_models" / "cache",
    allow_patterns=[
        "*.json",
        "*.py",
        "*.safetensors",
        "README.md",
        "LICENSE",
    ],
    max_workers=2,
)
print(destination)
