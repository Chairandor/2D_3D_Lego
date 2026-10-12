"""Check gated SAM 3 checkpoint access without downloading or printing credentials."""

from huggingface_hub import get_hf_file_metadata, get_token, hf_hub_url
from huggingface_hub.utils import HfHubHTTPError


url = hf_hub_url("facebook/sam3", "sam3.pt")
try:
    metadata = get_hf_file_metadata(url, token=get_token())
except HfHubHTTPError as exc:
    print(f"checkpoint accessible: False; HTTP status: {exc.response.status_code}")
else:
    print(f"checkpoint accessible: True; bytes: {metadata.size}")
