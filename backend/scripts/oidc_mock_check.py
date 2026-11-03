"""M12 端到端自检（无需 Casdoor）：本地起一个 mock OIDC 服务，
跑通「前端授权码 → 后端 /auth/oidc/exchange 换 id_token → 网关 decode_token 验签」。

用途：验证前端登录链路的后端部分是否就绪。真实联调仍需在 /setup 填 Casdoor 的 Client ID。
运行：backend 目录下 `.venv/Scripts/python.exe scripts/oidc_mock_check.py`
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

os.environ.setdefault("PG_PORT", "1")  # 隔离外部 PG，避免连本地库

PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_JWK = json.loads(RSAAlgorithm.to_jwk(PRIV.public_key()))
PUB_JWK["kid"] = "k1"
CLIENT = "rsp-client"
ISSUER = ""  # 端口确定后回填


class MockIdP(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/.well-known/openid-configuration":
            self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/login/oauth/authorize",
                "token_endpoint": f"{ISSUER}/api/login/oauth/access_token",
                "jwks_uri": f"{ISSUER}/.well-known/jwks",
            })
        elif self.path == "/.well-known/jwks":
            self._json({"keys": [PUB_JWK]})
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(n).decode())
        if self.path != "/api/login/oauth/access_token":
            return self._json({"error": "not_found"}, 404)
        if form.get("grant_type", [""])[0] != "authorization_code" or form.get("code", [""])[0] != "demo-code":
            return self._json({"error": "invalid_grant"}, 400)
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": ISSUER, "aud": CLIENT, "sub": "u_mock",
                "preferred_username": "mock.user", "name": "Mock User",
                "affiliation": "d_sales", "role": "employee",
                "iat": now, "exp": now + 3600,
            },
            PRIV, algorithm="RS256", headers={"kid": "k1"},
        )
        self._json({"access_token": "at", "id_token": id_token, "token_type": "Bearer", "expires_in": 3600})


def main() -> int:
    global ISSUER
    srv = HTTPServer(("127.0.0.1", 0), MockIdP)
    ISSUER = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from fastapi.testclient import TestClient
    from app.core import security
    from app.core.config import settings
    from app.main import app

    # 确保不受历史 env 干扰
    settings.OIDC_ISSUER = ""
    settings.OIDC_AUDIENCE = ""

    client = TestClient(app)
    r = client.post("/api/v1/auth/oidc/exchange", json={
        "issuer": ISSUER, "client_id": CLIENT,
        "redirect_uri": "http://127.0.0.1:5173/callback",
        "code": "demo-code", "code_verifier": "verifier-abc",
    })
    print(f"[1] exchange -> {r.status_code}")
    if r.status_code != 200:
        print("    FAIL:", r.text[:300])
        return 1
    data = r.json()["data"]
    print("    user:", data["user"])
    id_token = data["id_token"]

    caller = security.decode_token(id_token)
    print(f"[2] decode_token -> {caller}")
    if caller is None:
        print("    FAIL: 网关未接受 id_token")
        return 1

    # 负例：篡改签名必须被拒
    bad = security.decode_token(id_token + "x")
    print(f"[3] tampered token -> {bad} (期望 None)")
    ok = bad is None and caller.userId == "mock.user" and caller.deptId == "d_sales"

    srv.shutdown()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
