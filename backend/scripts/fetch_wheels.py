"""临时脚本：通过可用的 httpx 通道下载 wheel，供 pip --no-index 本地安装。

背景：本机 pip 走 HTTP_PROXY 时拿不到 index 分发列表（index 页可达但版本列表为空），
而 httpx 走同一代理正常。故用此脚本先把 wheel 落到本地目录。

用 PEP 691 的 simple JSON 接口（比 /pypi/{pkg}/json 小一个数量级，
后者对大包如 lxml 有几十 MB，会直接拖死）。
"""
import os
import sys

import httpx

PKGS = ["lxml", "pillow", "xlsxwriter", "python-docx", "python-pptx", "reportlab", "chardet"]
DEST = sys.argv[1] if len(sys.argv) > 1 else "wheels"
HDR = {"Accept": "application/vnd.pypi.simple.v1+json"}
TAGS = ["cp313-cp313-win_amd64", "cp313-abi3-win_amd64", "py3-none-any", "py2.py3-none-any"]


def pick(files):
    """按「版本倒序 + 优先级 tag」挑 wheel：先挑最新的平台专用二进制，再纯 python。

    simple 接口返回的 files 未按版本排序（实测会挑到 python-docx 1.0.0rc1 这种
    老版本），必须自己排序 —— 否则装上一堆古董包，API 差异要到运行时才炸。
    """
    from packaging.version import InvalidVersion, Version

    def vkey(fn):
        core = fn.split("-")[1] if fn.count("-") >= 2 else "0"
        try:
            return Version(core)
        except InvalidVersion:
            return Version("0")

    wheels = sorted(
        [(f["filename"], f["url"]) for f in files
         if f["filename"].endswith(".whl") and not vkey(f["filename"]).is_prerelease],
        key=lambda x: vkey(x[0]), reverse=True)
    for tag in TAGS:
        for fn, url in wheels:
            if tag in fn:
                return fn, url
    return None, None


def main():
    os.makedirs(DEST, exist_ok=True)
    with httpx.Client(timeout=90, follow_redirects=True, headers=HDR) as c:
        for pkg in PKGS:
            try:
                body = c.get(f"https://pypi.org/simple/{pkg}/").json()
            except Exception as e:
                print(f"FAIL meta {pkg}: {type(e).__name__} {e}", flush=True)
                continue
            fn, url = pick(body.get("files") or [])
            if not fn:
                print(f"FAIL pick {pkg}: 无匹配 wheel", flush=True)
                continue
            path = os.path.join(DEST, fn)
            if os.path.exists(path):
                print(f"SKIP {fn}", flush=True)
                continue
            with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(path, "wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
            print(f"OK   {fn} ({os.path.getsize(path) // 1024} KB)", flush=True)


if __name__ == "__main__":
    main()
