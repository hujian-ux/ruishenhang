"""把 M27 新增端点同步进手写契约 openapi.yaml（幂等）。

背景：项目 openapi.yaml 是评审期手写契约，不是 FastAPI 自动导出。M27 一次性
加了 6 组路由，手抄容易漏。这里从 `app.main.app.openapi()` 读出真实路由，
只把**尚未出现在契约里的路径**补成一段可解析的 YAML，并放在
`# M27-AUTOGEN-BEGIN/END` 标记之间，重复运行会整段替换而不是叠加。

请求体统一标 `{type: object, additionalProperties: true}`（字段级定义以各
router 的 Pydantic / 文档为准），响应统一引用 GenericResponse —— 与现有契约
「data 内部结构不约束」的惯例一致。

用法：cd backend && ./.venv/Scripts/python.exe scripts/sync_m27_openapi.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DOC = ROOT / "openapi.yaml"
BEGIN = "  # M27-AUTOGEN-BEGIN"
END = "  # M27-AUTOGEN-END"


def _param_line(p: dict) -> list[str]:
    name = p.get("name", "")
    where = p.get("in", "query")
    required = "true" if p.get("required") else "false"
    typ = ((p.get("schema") or {}).get("type") or "string")
    return [
        f"        - name: {name}",
        f"          in: {where}",
        f"          required: {required}",
        f"          schema: {{type: {typ}}}",
    ]


def build_block(existing_paths: set[str]) -> str:
    from app.core.config import settings  # noqa: E402
    from app.main import app              # noqa: E402  （需先设好 sys.path）

    spec = app.openapi()
    prefix = settings.API_PREFIX.rstrip("/")
    # app.openapi() 的路径带 include_router 的 prefix；手写契约的 servers.url
    # 已经是 /api/v1，paths 段必须**不带**前缀，否则会拼成 /api/v1/api/v1/...
    def strip_prefix(p: str) -> str:
        if p == prefix:
            return "/"
        return p[len(prefix):] if p.startswith(prefix + "/") else p

    # 同一 path 的多个方法必须在同一个 YAML 键下，重复键会被 safe_load 覆盖
    grouped: dict[str, dict] = {}
    for raw_path, methods in sorted(spec.get("paths", {}).items()):
        path = strip_prefix(raw_path)
        if path in existing_paths or path in ("/healthz", "/"):
            continue
        bucket = grouped.setdefault(path, {})
        for method, op in methods.items():
            if method.lower() in ("get", "post", "put", "delete", "patch"):
                bucket[method.lower()] = op

    lines = [BEGIN, "  # 以下由 scripts/sync_m27_openapi.py 从运行时路由自动生成；字段级定义见各 router",
             "  # 覆盖蓝图第 19/20/21/26/27/28/29/30/31/34/38/39/40/41/42/43/44 章缺口"]
    added = 0
    for path, methods in sorted(grouped.items()):
        lines.append(f"  {path}:")
        for method, op in methods.items():
            lines.append(f"    {method}:")
            tags = op.get("tags") or ["m27"]
            lines.append(f"      tags: [{', '.join(tags)}]")
            summary = (op.get("summary") or op.get("operationId") or path).replace(":", "：")
            lines.append(f"      summary: {summary}")
            lines.append(f"      operationId: {op.get('operationId') or (method.lower() + path.replace('/', '_').replace('{', '').replace('}', ''))}")
            params = op.get("parameters") or []
            if params:
                lines.append("      parameters:")
                for p in params:
                    lines.extend(_param_line(p))
            if op.get("requestBody"):
                lines.extend([
                    "      requestBody:",
                    "        content:",
                    "          application/json:",
                    "            schema: {type: object, additionalProperties: true}",
                ])
            lines.extend([
                "      responses:",
                "        '200':",
                "          description: 统一响应（code=0 表示成功）",
                "          content:",
                "            application/json:",
                "              schema: {$ref: '#/components/schemas/GenericResponse'}",
                "        '401': {$ref: '#/components/responses/Unauthorized'}",
            ])
            added += 1
    lines.append(END)
    print(f"[sync_m27_openapi] 新增/刷新 {added} 个操作")
    return "\n".join(lines) + "\n"


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    # 只在手写契约正文里找已有路径，别把上一次自动生成的块算进去
    handwritten = text.split(BEGIN, 1)[0]
    existing = {ln.strip().rstrip(":") for ln in handwritten.splitlines()
                if ln.startswith("  /") and ln.rstrip().endswith(":")}
    block = build_block(existing)
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        tail = tail.lstrip("\n")
        text = head + block + tail
    else:
        marker = "\ncomponents:\n"
        if marker not in text:
            print("openapi.yaml 里找不到 components: 锚点", file=sys.stderr)
            return 1
        text = text.replace(marker, "\n" + block + marker, 1)
    DOC.write_text(text, encoding="utf-8")
    import yaml
    parsed = yaml.safe_load(text)
    print(f"[sync_m27_openapi] openapi.yaml 现有 {len(parsed.get('paths', {}))} paths，YAML 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
