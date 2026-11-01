# -*- coding: utf-8 -*-
"""校验 openapi.yaml，并生成一份无依赖、可预览的 API 参考 HTML（静态渲染，便于直接查看/分享）。
openapi.yaml 为机器可读契约（可导入 Swagger UI / Postman / 代码生成）。
"""
import os, re, json
import yaml

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
SPEC = os.path.join(BASE, "openapi.yaml")
HTML_OUT = os.path.join(BASE, "接口契约参考.html")

with open(SPEC, encoding="utf-8") as f:
    spec = yaml.safe_load(f)

# ---- 基本校验 ----
paths = spec.get("paths", {})
schemas = spec.get("components", {}).get("schemas", {})
assert paths, "paths 为空"
assert schemas, "schemas 为空"
print("校验通过：paths =", len(paths), " schemas =", len(schemas))

def resolve(ref):
    """#/components/schemas/X -> dict"""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node[p]
    return node

def fmt_type(prop):
    t = prop.get("type", "object")
    if "enum" in prop:
        return "enum(" + ", ".join(map(str, prop["enum"])) + ")"
    if t == "array":
        items = prop.get("items", {})
        if "$ref" in items:
            return "array<" + items["$ref"].split("/")[-1] + ">"
        return "array<" + items.get("type", "object") + ">"
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    return t

def schema_fields(schema):
    """返回 [(name, type, required, desc)]; 支持 $ref。"""
    if "$ref" in schema:
        schema = resolve(schema["$ref"])
    req = set(schema.get("required", []))
    rows = []
    props = schema.get("properties", {})
    for name, p in props.items():
        rows.append((name, fmt_type(p), name in req, p.get("description", "")))
    return rows

def example_of(media):
    if not media:
        return None
    content = media.get("application/json", {})
    return content.get("example") or content.get("schema", {}).get("example")

# ---- 生成 HTML ----
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def table(rows):
    h = '<table class="tb"><thead><tr><th>字段</th><th>类型</th><th>必填</th><th>说明</th></tr></thead><tbody>'
    for name, t, rq, desc in rows:
        h += f'<tr><td class="mono">{esc(name)}</td><td class="mono">{esc(t)}</td><td>{"是" if rq else "否"}</td><td>{esc(desc)}</td></tr>'
    return h + "</tbody></table>"

parts = []
parts.append("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>接口契约参考 · 企业 AI 智能体中台</title>
<style>
 body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;background:#f5f7fa;color:#1f2d3d}
 header{background:#0a2540;color:#fff;padding:22px 28px}
 header h1{margin:0;font-size:22px}
 header p{margin:6px 0 0;color:#b9c7d6;font-size:13px}
 main{max-width:960px;margin:0 auto;padding:24px 20px 60px}
 .ep{background:#fff;border:1px solid #d7e0ea;border-radius:10px;padding:18px 20px;margin:18px 0;box-shadow:0 1px 3px rgba(10,37,64,.04)}
 .ep h2{margin:0 0 4px;font-size:17px;color:#0a2540}
 .method{display:inline-block;background:#1565c0;color:#fff;font-size:12px;font-weight:700;padding:3px 9px;border-radius:5px;margin-right:8px;vertical-align:middle}
 .path{font-family:Consolas,monospace;font-size:14px;color:#1565c0;font-weight:700}
 .desc{font-size:13px;color:#44566a;margin:8px 0 12px;line-height:1.6}
 .lbl{font-weight:700;font-size:13px;color:#0a2540;margin:14px 0 6px}
 .tb{width:100%;border-collapse:collapse;font-size:12.5px}
 .tb th{background:#1565c0;color:#fff;text-align:left;padding:6px 8px}
 .tb td{border:1px solid #e3e9f0;padding:5px 8px;vertical-align:top}
 .mono{font-family:Consolas,monospace;color:#0a2540}
 pre{background:#0f1b2d;color:#e6edf3;padding:14px;border-radius:8px;overflow:auto;font-size:12px;line-height:1.5;font-family:Consolas,monospace}
 .tag{display:inline-block;background:#eef4fb;color:#1565c0;font-size:11px;padding:2px 7px;border-radius:4px;margin-left:6px}
 footer{text-align:center;color:#8a9bad;font-size:12px;padding:20px}
</style></head><body>""")
parts.append(f"""<header><h1>{esc(spec['info']['title'])}</h1>
<p>版本 {esc(spec['info']['version'])} · 机器可读契约见 openapi.yaml（可导入 Swagger UI / Postman / 代码生成）· Base URL {esc(spec['servers'][0]['url'])}</p></header><main>""")

for path, methods in paths.items():
    for method, op in methods.items():
        tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in op.get("tags", []))
        parts.append(f'<div class="ep"><h2><span class="method">{method.upper()}</span><span class="path">{esc(path)}</span>{tags}</h2>')
        parts.append(f'<div class="desc">{esc(op.get("description",""))}</div>')
        # 路径/头参数
        params = op.get("parameters", [])
        if params:
            rows = []
            for p in params:
                if "$ref" in p:
                    rp = resolve(p["$ref"])
                    rows.append((rp.get("name", "?"), rp.get("in", ""), p.get("required", False), rp.get("description", "")))
                else:
                    rows.append((p.get("name", "?"), p.get("in", ""), p.get("required", False), p.get("description", "")))
            parts.append('<div class="lbl">请求参数（路径 / 头）</div>')
            parts.append(table(rows))
        # 请求体
        rb = op.get("requestBody", {})
        if rb.get("content", {}).get("application/json", {}).get("schema"):
            sch = rb["content"]["application/json"]["schema"]
            parts.append('<div class="lbl">请求体（application/json）</div>')
            parts.append(table(schema_fields(sch)))
        # 响应示例
        resp = op.get("responses", {}).get("200", {})
        ex = example_of(resp.get("content", {}))
        if ex is not None:
            parts.append('<div class="lbl">响应示例（200）</div>')
            parts.append(f'<pre>{esc(json.dumps(ex, ensure_ascii=False, indent=2))}</pre>')
        parts.append('</div>')

parts.append("""</main><footer>企业 AI 智能体中台 · 移动端 MVP 接口契约参考 · 与 openapi.yaml 同源 · 内部使用</footer></body></html>""")

with open(HTML_OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
print("SAVED:", HTML_OUT)
