"""从运行中的 FastAPI 应用重新导出 openapi.yaml，闭合契约漂移。

做法：
1. 调用 app.openapi() 拿到「应用真实会暴露」的全部 path（这是防 404 的唯一真相源）。
2. 把 /api/v1 全局前缀从每条 path 摘掉，统一用 servers 表达（沿用原文件的 OpenAPI 约定）。
3. 保留原文件精心写好的 info（含中文说明）/ tags，只刷新 paths/components，避免丢文档价值。
4. 确保 securitySchemes 含 bearerAuth 且顶层 security 引用它。
"""
import os
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OA = os.path.join(ROOT, "openapi.yaml")
PREFIX = "/api/v1"


def main():
    # 延迟导入：必须在 backend 目录、venv 下执行
    from app.main import app
    spec = app.openapi()

    # 1) 摘前缀，路径归一为相对形式
    new_paths = {}
    for p, v in spec.get("paths", {}).items():
        np = p[len(PREFIX):] if p.startswith(PREFIX) else p
        if np == "":
            np = "/"
        new_paths[np] = v
    spec["paths"] = new_paths

    # 2) servers 约定
    spec["servers"] = [{"url": PREFIX, "description": "统一 API 网关前缀"}]

    # 3) 保留原 info / tags 文档价值
    with open(OA, "r", encoding="utf-8") as f:
        old = yaml.safe_load(f)
    spec["info"] = old.get("info", spec.get("info", {}))
    if old.get("tags"):
        spec["tags"] = old["tags"]

    # 4) 确保鉴权方案齐全
    comps = spec.setdefault("components", {})
    schemes = comps.setdefault("securitySchemes", {})
    schemes.setdefault("bearerAuth", {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    })
    spec.setdefault("security", [{"bearerAuth": []}])

    # 排序稳定（按 path 字母序），方便 diff
    ordered = {k: spec["paths"][k] for k in sorted(spec["paths"].keys())}
    spec["paths"] = ordered

    with open(OA, "w", encoding="utf-8") as f:
        yaml.safe_dump(spec, f, allow_unicode=True, sort_keys=False, width=4096)

    print(f"paths written: {len(spec['paths'])}")
    print(f"components.schemas: {len(spec.get('components', {}).get('schemas', {}))}")


if __name__ == "__main__":
    main()
