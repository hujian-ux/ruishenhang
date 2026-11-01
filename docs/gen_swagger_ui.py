# -*- coding: utf-8 -*-
"""把 openapi.yaml 渲染成 Swagger UI 交互页（Try-It-Out 可用）。
做法：读取 yaml -> 转 JSON -> 内联进 HTML，Swagger UI 库走 CDN。
仅需联网加载 UI 库；接口定义本身离线内嵌，无需本地文件 fetch。
"""
import os, json
import yaml

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
SRC = os.path.join(BASE, "openapi.yaml")
OUT = os.path.join(BASE, "接口契约SwaggerUI.html")

with open(SRC, encoding="utf-8") as f:
    spec = yaml.safe_load(f)

# 去掉 yaml 里可能的 Python 对象，确保可 JSON 序列化
spec_json = json.dumps(spec, ensure_ascii=False, indent=2)

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>接口契约 · Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css" />
  <style>
    body { margin: 0; }
    .topbar { background:#0a2540; color:#fff; padding:10px 20px; font-family:system-ui,'Microsoft YaHei',sans-serif; }
    .topbar b { font-size:15px; }
    .topbar span { color:#9fc0e8; font-size:12px; margin-left:10px; }
    #swagger-ui { margin-top:0; }
    .swagger-ui .topbar { display:none; }
  </style>
</head>
<body>
  <div class="topbar"><b>企业 AI 智能体中台 · 后端接口契约</b><span>Swagger UI 交互页 · 可在线调试（Try it out）</span></div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-standalone-preset.js"></script>
  <script>
    const SPEC = __SPEC__;
    window.onload = function () {
      window.ui = SwaggerUIBundle({
        spec: SPEC,
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [ SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset ],
        layout: 'StandaloneLayout',
        tryItOutEnabled: true
      });
    };
  </script>
</body>
</html>
"""

out = HTML.replace("__SPEC__", spec_json)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(out)
print("SAVED:", OUT)
print("paths:", len(spec.get("paths", {})), "schemas:", len(spec.get("components", {}).get("schemas", {})))
