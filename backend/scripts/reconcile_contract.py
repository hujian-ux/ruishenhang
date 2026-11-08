#!/usr/bin/env python3
"""OpenAPI 契约 ↔ 代码路由 精确对账工具（v2，修正全局前缀）。

要点：
- 所有 router 在 main.py 以 ``prefix=settings.API_PREFIX``（默认 /api/v1）挂载；
  openapi.yaml 是旧版生成、path 未带 /api/v1。因此比较前统一剥离全局前缀，避免假阳性。
- 只匹配真正的 ``router = APIRouter(prefix=...)`` 赋值（避开文档字符串里的同名注释）。
- 归一化：去重斜杠、剥离前缀、{param} 占位统一。

用法：
  backend/.venv/Scripts/python.exe scripts/reconcile_contract.py
"""
import glob
import os
import re
import sys

# __file__ = <project>/backend/scripts/reconcile_contract.py
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../backend
ROOT = os.path.dirname(BACKEND)                                       # .../<project>
OA = os.path.join(ROOT, "openapi.yaml")
METHODS = ["get", "post", "put", "delete", "patch", "head", "options"]


def norm(p: str) -> str:
    p = p.strip()
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/+", "/", p)
    p = re.sub(r"^/api/v1", "", p) or "/"   # 剥离全局前缀，统一比较基准
    p = re.sub(r"\{[^}]+\}", "{p}", p)      # 任意路径参数归一
    return p.rstrip("/") or "/"


def load_openapi_paths():
    try:
        import yaml
        with open(OA, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        paths = {norm(p) for p in spec.get("paths", {})}
        return paths, "yaml"
    except Exception:
        paths = set()
        with open(OA, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^  (/[^\s:]+):", line)
                if m:
                    paths.add(norm(m.group(1)))
        return paths, "regex"


def load_global_prefix() -> str:
    try:
        cfg = open(os.path.join(BACKEND, "app", "core", "config.py"), encoding="utf-8").read()
        m = re.search(r'API_PREFIX\s*=\s*_env\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']', cfg)
        if m:
            return m.group(2)
    except Exception:
        pass
    return "/api/v1"


def load_code_routes():
    routes = {}
    files = glob.glob(os.path.join(BACKEND, "app", "routers", "*.py"))
    files.append(os.path.join(BACKEND, "app", "main.py"))
    route_re = re.compile(r"@[\w]+\.(?:" + "|".join(METHODS) + r")\(\s*[\"']([^\"']*)[\"']")
    # 只匹配真正的赋值：router = APIRouter(prefix="...")
    rtr_re = re.compile(r"\brouter\s*=\s*APIRouter\(\s*(?:[^)]*?)prefix\s*=\s*[\"']([^\"']*)[\"']", re.S)
    for fp in files:
        if not os.path.exists(fp):
            continue
        src = open(fp, encoding="utf-8").read()
        m = rtr_re.search(src)
        rprefix = m.group(1) if m else ""
        for d in route_re.finditer(src):
            full = norm(f"{rprefix}/{d.group(1)}")
            routes.setdefault(full, set()).add(os.path.basename(fp))
    return routes


def main() -> int:
    oa_paths, method = load_openapi_paths()
    apfx = load_global_prefix()
    code_routes = load_code_routes()
    code_set = set(code_routes)

    declared_only = sorted(oa_paths - code_set)   # 声明有，代码无 → 404 风险
    code_only = sorted(code_set - oa_paths)       # 代码有，契约没写 → 漂移

    print(f"OpenAPI 解析方式: {method} | 全局前缀(API_PREFIX): {apfx}")
    print(f"OpenAPI paths (归一化): {len(oa_paths)}")
    print(f"代码 routes  (归一化): {len(code_set)}")
    print("=" * 64)
    print(f"① 声明在 OpenAPI、但代码无对应 handler（404 风险）: {len(declared_only)}")
    for p in declared_only:
        print("   [MISSING] ", p)
    if not declared_only:
        print("   （无，声明全部有实现）")
    print("-" * 64)
    print(f"② 代码有 handler、但未在 OpenAPI 声明（契约漂移/未文档化）: {len(code_only)}")
    for p in code_only:
        print(f"   [UNDOC]   {p}   <- {', '.join(sorted(code_routes[p]))}")
    if not code_only:
        print("   （无，代码全部已声明）")
    print("=" * 64)
    # 退出码：发现 404 风险时非 0（CI 卡点）
    return 1 if declared_only else 0


if __name__ == "__main__":
    sys.exit(main())
