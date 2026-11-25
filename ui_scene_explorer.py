"""Forward the local Scene Explorer to the existing OpenMask3D web API."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PREFIX = "/scene-explorer"
LIMIT = 128 * 1024 ** 2
SCENE_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
READ_API = re.compile(r"/api/(?:scenes|scene|instances/\d{1,9}/(?:preview|download\.ply)|frames/\d{1,9}\.jpg|crops/crop\d+_\d+_\d+\.png)")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def upstream_url(path: str, query: str = "", *, search: bool = False) -> str:
    """Map only recognised Scene Explorer API calls to the configured server."""
    supported = path == "/api/query" if search else path == "/static/plotly.min.js" or READ_API.fullmatch(path)
    if not supported:
        raise ValueError("未知的场景探索接口")
    params = parse_qs(query, keep_blank_values=True)
    if params and (search or set(params) != {"scene"} or len(params["scene"]) != 1 or not SCENE_ID.fullmatch(params["scene"][0])):
        raise ValueError("无效的场景编号")
    address = urlsplit(os.environ.get("OPENMASK3D_VIEWER_URL", "http://127.0.0.1:18765/"))
    if (address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password
            or address.query or address.fragment):
        raise ValueError("请把 OPENMASK3D_VIEWER_URL 设置为 OpenMask3D 服务地址")
    url = address.geturl().rstrip("/") + path
    return url + ("?" + urlencode({"scene": params["scene"][0]}) if params else "")


def fetch(path: str, query: str = "", body: dict | None = None) -> tuple[int, str, str | None, bytes]:
    """Read one bounded response from the configured OpenMask3D service."""
    url = upstream_url(path, query, search=body is not None)
    options = {} if body is None else {"data": json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                      "headers": {"Content-Type": "application/json"}}
    request = Request(url, **options)
    try:
        try:
            response = build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=45)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise RuntimeError("OpenMask3D 地址返回了跳转，请配置服务的实际地址") from exc
            response = exc
        with response:
            if int(response.headers.get("Content-Length", "0")) > LIMIT:
                raise ValueError("场景服务返回的文件超过 128 MiB")
            data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError("场景服务返回的文件超过 128 MiB")
            return response.code, response.headers.get_content_type(), response.headers.get("Content-Disposition"), data
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise RuntimeError("无法连接 OpenMask3D 服务，请确认已有网页服务和 SSH 隧道可用") from exc


def download_instance(scene: str, instance: int) -> tuple[str, bytes]:
    """Load the exact PLY associated with a selected scene and instance ID."""
    if not SCENE_ID.fullmatch(scene) or type(instance) is not int or not 0 <= instance <= 999_999_999:
        raise ValueError("请选择有效的场景实例")
    status, _, _, data = fetch(f"/api/instances/{instance}/download.ply", urlencode({"scene": scene}))
    if status != 200 or not data.startswith((b"ply\n", b"ply\r\n")):
        raise ValueError("目标实例的彩色 PLY 下载失败，请重新查询并选择该实例")
    return f"{scene}_instance_{instance}.ply", data
