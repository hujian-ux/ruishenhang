"""
临时 H5 开发/演示聚合服务器（非生产）。

把「前端静态 dist」+「后端 API」包进同一个本地端口（默认 8088），便于用一条
cloudflared 隧道同时暴露 H5 与 /api，手机端同源访问免 CORS。

生产请用 deploy/Caddyfile（自动 HTTPS 单源），不要长期跑这个脚本。

运行（backend venv，已有 fastapi + requests）：
    .venv/Scripts/python.exe ../deploy/h5_dev_server.py
"""
import os
import sys

import requests
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
import uvicorn

DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web", "dist"))
BACKEND = os.environ.get("H5_BACKEND", "http://127.0.0.1:8080").rstrip("/")
PORT = int(os.environ.get("H5_PORT", "8088"))

app = FastAPI(title="H5 Dev Aggregate")


def _static(path: str):
    if not path or path == "/":
        return FileResponse(os.path.join(DIST, "index.html"))
    full = os.path.normpath(os.path.join(DIST, path))
    if not full.startswith(DIST):
        return FileResponse(os.path.join(DIST, "index.html"))
    if os.path.isfile(full):
        return FileResponse(full)
    # SPA history 回退
    return FileResponse(os.path.join(DIST, "index.html"))


# ── 反代：API 与 healthz 转发到后端 ────────────────────────────────────────
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.api_route("/healthz", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request):
    url = BACKEND + request.url.path
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}
    try:
        resp = requests.request(
            request.method, url,
            headers=headers, params=dict(request.query_params),
            data=body, timeout=60,
        )
    except requests.RequestException as e:
        return Response(content=f"backend unreachable: {e}".encode(), status_code=502)
    skip = ("content-encoding", "content-length", "transfer-encoding", "connection")
    h = {k: v for k, v in resp.headers.items() if k.lower() not in skip}
    return Response(content=resp.content, status_code=resp.status_code, headers=h)


# ── 静态：其余走 dist（SPA 回退） ───────────────────────────────────────────
@app.api_route("/{path:path}", methods=["GET", "HEAD"])
async def static(path: str):
    return _static(path)


if __name__ == "__main__":
    if not os.path.isdir(DIST):
        sys.exit(f"dist 不存在: {DIST} —— 请先 `cd web && npm run build`")
    print(f"[h5_dev_server] serving dist={DIST}")
    print(f"[h5_dev_server] proxy /api,/healthz -> {BACKEND}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
