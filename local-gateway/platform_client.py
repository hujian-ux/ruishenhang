"""本地文件网关 · 平台客户端。

负责：登录拿 token（或直用配置 token）、把本地文件 multipart 上传到平台的
``POST /agent/files``（也可配 endpoint 指向其他接收端）。

设计：真实网络走 ``requests``；同时允许注入 ``http`` 回调，便于离线单测
（不触发任何网络）。鉴权头按平台约定发**裸 token**（不带 ``Bearer ``）。
"""
from __future__ import annotations

import mimetypes
from typing import Any, Callable, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover - 仅在没有 requests 时触发
    requests = None  # type: ignore


class PlatformClient:
    def __init__(self, base_url: str, token: Optional[str] = None,
                 tenant: Optional[str] = None,
                 http: Optional[Callable] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tenant = tenant
        # 注入的 http(method, url, **kw) -> {"status":int, "json":dict}
        self._http = http

    # ── 鉴权 ──
    def login(self, code: str = "dev_admin") -> Optional[str]:
        """用开发/服务令牌换登录态（演示/内部用）。生产应直配 token。"""
        r = self._call("POST", "/auth/sso/callback", json={
            "provider": "wecom", "code": code,
            "device_id": "local-gateway", "state": "x"})
        self.token = (r.get("json") or {}).get("data", {}).get("token") or self.token
        return self.token

    def _headers(self) -> Dict[str, str]:
        h = {}
        if self.token:
            h["Authorization"] = self.token
        if self.tenant:
            h["X-Tenant-Id"] = self.tenant
        return h

    # ── 底层调用 ──
    def _call(self, method: str, path: str, json: Optional[dict] = None,
              files: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.base_url + path
        if self._http:
            return self._http(method, url, json=json, files=files)
        if requests is None:
            raise RuntimeError("未安装 requests，无法发起真实请求（请 pip install requests）")
        resp = requests.request(method, url, json=json, files=files,
                                headers=self._headers(), timeout=30)
        try:
            body = resp.json()
        except Exception:
            body = {"detail": resp.text}
        return {"status": resp.status_code, "json": body}

    # ── 上传 ──
    def upload_file(self, path: str, title: Optional[str] = None,
                    endpoint: str = "/agent/files") -> Dict[str, Any]:
        """multipart 上传单个文件，返回平台响应。"""
        name = title or _basename(path)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if self._http:
            with open(path, "rb") as f:
                data = f.read()
            return self._http("POST", self.base_url + endpoint,
                              files={"file": (name, data, ctype)})
        with open(path, "rb") as f:
            resp = requests.post(self.base_url + endpoint,
                                 files={"file": (name, f, ctype)},
                                 headers=self._headers(), timeout=60)
        try:
            body = resp.json()
        except Exception:
            body = {"detail": resp.text}
        return {"status": resp.status_code, "json": body}


def _basename(path: str) -> str:
    import os
    return os.path.basename(path)
