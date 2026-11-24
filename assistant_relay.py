"""Connection settings shared by the stage assistant's relay requests."""

import os
import urllib.parse
import urllib.request


USER_AGENT = "codex_cli_rs/0.77.0 (Windows 10.0.26100; x86_64) WindowsTerminal"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not send the relay credential to a redirected address.
        return None


def base_url() -> str:
    value = os.environ.get("ASSISTANT_RELAY_BASE_URL", "").strip().rstrip("/")
    url = urllib.parse.urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise RuntimeError("阶段助手的中转站地址必须是 HTTPS API 地址")
    return value
