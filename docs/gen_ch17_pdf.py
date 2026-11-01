# -*- coding: utf-8 -*-
"""单独导出「第 17 章 移动端 MVP 开发清单（含 17.6 接口字段级定义）」为 A4 PDF，
供技术排期与后端直接开发。中文：msyh.ttc / msyhbd.ttc。
代码块（含中文注释）用 YaHei 小字号 + 浅色底卡片呈现，避免等宽字体缺中文。
"""
import os, re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
F_MSYH = r"C:\Windows\Fonts\msyh.ttc"
F_MSYHBD = r"C:\Windows\Fonts\msyhbd.ttc"
pdfmetrics.registerFont(TTFont("YaHei", F_MSYH, subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHei-Bold", F_MSYHBD, subfontIndex=0))
registerFontFamily("YaHei", normal="YaHei", bold="YaHei-Bold",
                   italic="YaHei", boldItalic="YaHei-Bold")

NAVY = colors.HexColor("#0a2540")
BLUE = colors.HexColor("#1565c0")
INK = colors.HexColor("#1f2d3d")
MUTED = colors.HexColor("#5b7185")
LINE = colors.HexColor("#d7e0ea")
HEADBG = colors.HexColor("#1565c0")
CARDBG = colors.HexColor("#eef4fb")
CODEBG = colors.HexColor("#f4f6f9")

def styles():
    return {
        "title": ParagraphStyle("title", fontName="YaHei-Bold", fontSize=17,
                                textColor=NAVY, alignment=TA_CENTER, leading=21),
        "sub": ParagraphStyle("sub", fontName="YaHei", fontSize=9.5,
                              textColor=MUTED, alignment=TA_CENTER, leading=13),
        "h2": ParagraphStyle("h2", fontName="YaHei-Bold", fontSize=12.5,
                             textColor=NAVY, leading=16, spaceBefore=8),
        "h3": ParagraphStyle("h3", fontName="YaHei-Bold", fontSize=10.5,
                             textColor=BLUE, leading=14, spaceBefore=5),
        "intro": ParagraphStyle("intro", fontName="YaHei", fontSize=9.5,
                                textColor=INK, leading=13.5, alignment=TA_LEFT),
        "body": ParagraphStyle("body", fontName="YaHei", fontSize=8.5,
                               textColor=INK, leading=11.5, alignment=TA_LEFT),
        "bullet": ParagraphStyle("bullet", fontName="YaHei", fontSize=8.5,
                                 textColor=INK, leading=12, alignment=TA_LEFT,
                                 leftIndent=10),
        "hcell": ParagraphStyle("hcell", fontName="YaHei-Bold", fontSize=8.5,
                                textColor=colors.white, leading=11),
        "note": ParagraphStyle("note", fontName="YaHei", fontSize=8,
                               textColor=MUTED, leading=11),
        "code": ParagraphStyle("code", fontName="YaHei", fontSize=7.3,
                               textColor=colors.HexColor("#1f2d3d"),
                               leading=9.6, alignment=TA_LEFT),
        "lbl": ParagraphStyle("lbl", fontName="YaHei-Bold", fontSize=8.5,
                              textColor=NAVY, leading=11.5),
    }

def Hcell(t):
    return Paragraph(t, S["hcell"])

def build_table(data, colWidths, S, header_rows=1):
    t = Table(data, colWidths=colWidths, repeatRows=header_rows)
    style = [
        ("BACKGROUND", (0,0), (-1, header_rows-1), HEADBG),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.5, LINE),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("BACKGROUND", (0, header_rows), (-1,-1), colors.white),
    ]
    t.setStyle(TableStyle(style))
    return t

def code_block(text, S):
    """代码块：浅底卡片 + 小字号 YaHei（兼容中文注释）。"""
    inner = Paragraph(text.replace(" ", "&nbsp;").replace("\n", "<br/>"), S["code"])
    t = Table([[inner]], colWidths=[S["_cw"]])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), CODEBG),
        ("BOX", (0,0), (-1,-1), 0.5, LINE),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    return t

def bullets(items, S):
    return [Paragraph("• " + x, S["bullet"]) for x in items]

S = styles()

doc = SimpleDocTemplate(os.path.join(BASE, "移动端MVP开发清单_A4.pdf"), pagesize=A4,
                        leftMargin=16*mm, rightMargin=16*mm,
                        topMargin=15*mm, bottomMargin=15*mm,
                        title="移动端 MVP 开发清单")
# 内容可用宽度（A4 210 - 左右 16*2 = 178mm）
S["_cw"] = 178*mm
e = []
e.append(Paragraph("移动端 MVP 开发清单", S["title"]))
e.append(Paragraph("企业 AI 智能体中台 · 第 17 章 · 给技术排期 / 后端直接开发（A4）", S["sub"]))
e.append(Spacer(1, 5*mm))

e.append(Paragraph(
    "本章给出移动端 MVP（以企微/钉钉/飞书 H5 嵌入为起步形态）的<b>页面、接口、权限点</b>清单，"
    "并附<b>接口字段级定义</b>供后端直接开发。核心原则：移动端与 PC 端共用同一套后端与权限层，"
    "H5 仅做轻量前端，不重复实现召回过滤与 RBAC。", S["intro"]))
e.append(Spacer(1, 3*mm))

# 17.1
e.append(Paragraph("17.1 页面清单（MVP）", S["h2"]))
d1 = [[Hcell("页面"), Hcell("功能"), Hcell("优先级"), Hcell("依赖")],
      [Paragraph("<b>工作台首页</b>", S["body"]),
       Paragraph("快捷入口、待办、搜索/语音提问、我的知识", S["body"]),
       Paragraph("P0", S["body"]), Paragraph("SSO、待办接口、知识检索", S["body"])],
      [Paragraph("<b>Agent 对话页</b>", S["body"]),
       Paragraph("语音/文字提问、卡片化结果、引用来源、一句话触发工作流", S["body"]),
       Paragraph("P0", S["body"]), Paragraph("Agent 服务、RAG 召回（带权限）", S["body"])],
      [Paragraph("<b>审批页</b>", S["body"]),
       Paragraph("入驻/共享/导出申请一键通过驳回、已处理记录", S["body"]),
       Paragraph("P0", S["body"]), Paragraph("审批流、权限校验", S["body"])],
      [Paragraph("<b>知识检索页</b>", S["body"]),
       Paragraph("搜制度/产品/案例，带可见范围标识的结果", S["body"]),
       Paragraph("P1", S["body"]), Paragraph("RAG 召回、权限标识", S["body"])],
      [Paragraph("<b>我的知识页</b>", S["body"]),
       Paragraph("个人/部门知识查看与轻量编辑", S["body"]),
       Paragraph("P1", S["body"]), Paragraph("知识库读写接口", S["body"])],
      [Paragraph("<b>用量看板（移动简版）</b>", S["body"]),
       Paragraph("本部门用量、告警", S["body"]),
       Paragraph("P2", S["body"]), Paragraph("统计接口", S["body"])]]
e.append(build_table(d1, [30*mm, 78*mm, 18*mm, 52*mm], S))
e.append(Spacer(1, 3*mm))

# 17.2
e.append(Paragraph("17.2 接口 / 能力清单", S["h2"]))
d2 = [[Hcell("接口 / 能力"), Hcell("说明"), Hcell("权限约束"), Hcell("优先级")],
      [Paragraph("<b>SSO / OAuth 登录</b>", S["body"]),
       Paragraph("对接企业 IM 网页授权，获取员工身份与部门", S["body"]),
       Paragraph("设备绑定、二次验证", S["body"]), Paragraph("P0", S["body"])],
      [Paragraph("<b>带权召回 / RAG 检索</b>", S["body"]),
       Paragraph("与 PC 同一召回服务，注入调用者身份，返回授权片段", S["body"]),
       Paragraph("scope/owner/dept 过滤（不可绕过）", S["body"]), Paragraph("P0", S["body"])],
      [Paragraph("<b>Agent 触发</b>", S["body"]),
       Paragraph("自然语言映射工作流，调用带权工具", S["body"]),
       Paragraph("调用者上下文随任务下发", S["body"]), Paragraph("P0", S["body"])],
      [Paragraph("<b>审批 API</b>", S["body"]),
       Paragraph("查询待办、通过/驳回、记录操作人", S["body"]),
       Paragraph("仅平台管理员/授权人可审批", S["body"]), Paragraph("P0", S["body"])],
      [Paragraph("<b>知识读写（个人/部门）</b>", S["body"]),
       Paragraph("新建/编辑/删除个人与部门知识", S["body"]),
       Paragraph("受 RBAC 与 scope 约束", S["body"]), Paragraph("P1", S["body"])],
      [Paragraph("<b>用量统计</b>", S["body"]),
       Paragraph("部门/个人用量、成本分摊", S["body"]),
       Paragraph("部门看本部门、平台看全部", S["body"]), Paragraph("P2", S["body"])],
      [Paragraph("<b>审计日志</b>", S["body"]),
       Paragraph("操作留痕、导出审批记录", S["body"]),
       Paragraph("平台管理员/授权人可见", S["body"]), Paragraph("P1", S["body"])]]
e.append(build_table(d2, [33*mm, 66*mm, 47*mm, 18*mm], S))
e.append(Spacer(1, 3*mm))

# 17.3
e.append(Paragraph("17.3 移动端权限点清单", S["h2"]))
d3 = [[Hcell("权限点"), Hcell("说明"), Hcell("校验位置")],
      [Paragraph("登录身份", S["body"]), Paragraph("IM 网页授权获取 userId / deptId", S["body"]), Paragraph("SSO 网关", S["body"])],
      [Paragraph("可访问部门空间", S["body"]), Paragraph("仅本人所属部门 + 授权共享部门", S["body"]), Paragraph("RBAC + 召回层", S["body"])],
      [Paragraph("可见知识范围", S["body"]), Paragraph("全域公开 + 本部门 + 个人", S["body"]), Paragraph("召回过滤（同 PC）", S["body"])],
      [Paragraph("审批资格", S["body"]), Paragraph("平台管理员 / 部门管理员 / 授权人", S["body"]), Paragraph("审批 API", S["body"])],
      [Paragraph("敏感页操作", S["body"]), Paragraph("高敏部门可选禁截屏/禁复制", S["body"]), Paragraph("前端策略 + 后端校验", S["body"])],
      [Paragraph("导出审批", S["body"]), Paragraph("敏感导出走审批流", S["body"]), Paragraph("审批 API + 审计", S["body"])]]
e.append(build_table(d3, [33*mm, 95*mm, 50*mm], S))
e.append(Spacer(1, 3*mm))

# 17.4
e.append(Paragraph("17.4 技术排期建议（MVP 4–6 周）", S["h2"]))
d4 = [[Hcell("周次"), Hcell("任务"), Hcell("产出")],
      [Paragraph("W1", S["body"]), Paragraph("SSO 对接 IM、H5 框架搭建、登录与设备绑定", S["body"]), Paragraph("可登录 H5", S["body"])],
      [Paragraph("W2", S["body"]), Paragraph("复用 PC 召回/RAG 与 Agent 服务，接入对话页", S["body"]), Paragraph("手机可问答、可触发 Agent", S["body"])],
      [Paragraph("W3", S["body"]), Paragraph("工作台首页 + 搜索/语音 + 待办聚合", S["body"]), Paragraph("首页可用", S["body"])],
      [Paragraph("W4", S["body"]), Paragraph("审批页（通过/驳回/记录）联调", S["body"]), Paragraph("移动审批闭环", S["body"])],
      [Paragraph("W5", S["body"]), Paragraph("权限一致性联调、越权用例验证、安全（水印/禁截屏）", S["body"]), Paragraph("安全验收通过", S["body"])],
      [Paragraph("W6", S["body"]), Paragraph("试点部门真实任务验收 + MVP 复盘", S["body"]), Paragraph("MVP 交付", S["body"])]]
e.append(build_table(d4, [18*mm, 100*mm, 60*mm], S))
e.append(Spacer(1, 3*mm))

# 17.5
e.append(Paragraph("17.5 验收标准", S["h2"]))
for b in bullets([
    "手机端与 PC 端同一账号、同一可见范围（跨部门 / 越级不可见）",
    "问答结果与引用来源一致，召回不绕过权限层",
    "审批操作留痕、可回溯；敏感导出走审批",
    "弱网下核心问答可用（预缓存命中）",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 17.6
e.append(Paragraph("17.6 接口字段级定义（给后端直接开发）", S["h2"]))
e.append(Paragraph(
    "以下为移动端 MVP 与 PC 共用后端的核心接口字段定义。所有接口均在<b>网关层注入调用者身份</b>"
    "（userId / deptId / role），召回类接口统一走第 18 章的<b>单一过滤点</b>，前端不持有越权数据。"
    "基础约定：Base URL = /api/v1；鉴权 Authorization: Bearer &lt;token&gt;；时间字段统一 ISO-8601 (UTC)；分页 page / pageSize。",
    S["intro"]))
e.append(Spacer(1, 2*mm))

# 17.6.1
e.append(Paragraph("17.6.1 带权召回 / RAG 检索（核心，不可绕过）", S["h3"]))
e.append(Paragraph("POST /api/v1/rag/retrieve —— 与 PC 同一召回服务，注入调用者身份后在召回阶段按 scope / owner / dept 过滤。", S["body"]))
e.append(Spacer(1, 1.5*mm))
d51 = [[Hcell("请求字段"), Hcell("类型"), Hcell("必填"), Hcell("说明")],
       [Paragraph("query", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("用户自然语言问题（经脱敏后入参）", S["body"])],
       [Paragraph("kb_scope", S["code"]), Paragraph("enum", S["code"]), Paragraph("否", S["body"]), Paragraph("all / department / personal，默认 all（仍受身份约束）", S["body"])],
       [Paragraph("top_k", S["code"]), Paragraph("int", S["code"]), Paragraph("否", S["body"]), Paragraph("返回片段数，默认 6，上限 20", S["body"])],
       [Paragraph("caller", S["code"]), Paragraph("object", S["code"]), Paragraph("是", S["body"]), Paragraph("{userId, deptId, role}（前端不可伪造）", S["body"])],
       [Paragraph("tenant_id", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("企业租户隔离", S["body"])]]
e.append(build_table(d51, [28*mm, 20*mm, 14*mm, 116*mm], S))
e.append(Spacer(1, 1.5*mm))
e.append(Paragraph("响应示例：", S["lbl"]))
e.append(code_block(
'{\n'
'  "code": 0,\n'
'  "data": {\n'
'    "query": "五一活动话术",\n'
'    "hits": [\n'
'      {\n'
'        "doc_id": "doc_8831",\n'
'        "title": "五一营销活动方案 v3",\n'
'        "snippet": "建议分三档：① 到店礼 ② 满减 ③ 老带新",\n'
'        "scope": "department",        // 命中后回填，前端用于展示可见范围\n'
'        "kb_tier": "department",\n'
'        "score": 0.91,\n'
'        "source": "产品手册 v3",\n'
'        "can_access": true            // 已通过过滤点，必然为 true\n'
'      }\n'
'    ],\n'
'    "filtered_out": 3,                // 被过滤的越权片段数（审计用，不返回内容）\n'
'    "trace_id": "trace_2026abc"\n'
'  }\n'
'}', S))
e.append(Spacer(1, 3*mm))

# 17.6.2
e.append(Paragraph("17.6.2 SSO / OAuth 登录（对接企业 IM）", S["h3"]))
e.append(Paragraph("POST /api/v1/auth/sso/callback —— 接收企微/钉钉/飞书网页授权 code，换取员工身份与部门，并做设备绑定。", S["body"]))
e.append(Spacer(1, 1.5*mm))
d52 = [[Hcell("请求字段"), Hcell("类型"), Hcell("必填"), Hcell("说明")],
       [Paragraph("provider", S["code"]), Paragraph("enum", S["code"]), Paragraph("是", S["body"]), Paragraph("wecom / dingtalk / feishu", S["body"])],
       [Paragraph("code", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("IM 回调授权码（一次性）", S["body"])],
       [Paragraph("device_id", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("设备指纹，用于绑定与换机二次验证", S["body"])],
       [Paragraph("state", S["code"]), Paragraph("string", S["code"]), Paragraph("否", S["body"]), Paragraph("防 CSRF", S["body"])]]
e.append(build_table(d52, [28*mm, 20*mm, 14*mm, 116*mm], S))
e.append(Spacer(1, 1.5*mm))
e.append(Paragraph("响应示例：", S["lbl"]))
e.append(code_block(
'{\n'
'  "code": 0,\n'
'  "data": {\n'
'    "token": "&lt;jwt&gt;",\n'
'    "expires_in": 7200,\n'
'    "user": { "userId": "u_1024", "name": "张三", "deptId": "d_sales", "role": "employee" },\n'
'    "device_bound": true,\n'
'    "need_stepup": false             // 换设备时为 true，需二次验证\n'
'  }\n'
'}', S))
e.append(Spacer(1, 3*mm))

# 17.6.3
e.append(Paragraph("17.6.3 Agent 触发（自然语言映射工作流）", S["h3"]))
e.append(Paragraph("POST /api/v1/agent/invoke —— 调用者上下文（caller）随任务下发，Agent 调用的工具同样走带权召回。", S["body"]))
e.append(Spacer(1, 1.5*mm))
d53 = [[Hcell("请求字段"), Hcell("类型"), Hcell("必填"), Hcell("说明")],
       [Paragraph("agent_id", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("目标 Agent（如 sales_script）", S["body"])],
       [Paragraph("input", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("用户输入（语音经 ASR 转写后传入）", S["body"])],
       [Paragraph("caller", S["code"]), Paragraph("object", S["code"]), Paragraph("是", S["body"]), Paragraph("{userId, deptId, role}，工具调用继承此身份", S["body"])],
       [Paragraph("stream", S["code"]), Paragraph("bool", S["code"]), Paragraph("否", S["body"]), Paragraph("是否 SSE 流式，默认 true", S["body"])]]
e.append(build_table(d53, [28*mm, 20*mm, 14*mm, 116*mm], S))
e.append(Spacer(1, 1.5*mm))
e.append(Paragraph("响应示例（SSE 末帧）：", S["lbl"]))
e.append(code_block(
'{\n'
'  "code": 0,\n'
'  "data": {\n'
'    "answer": "建议分三档执行五一活动：到店礼、满减、老带新……",\n'
'    "citations": [ { "doc_id": "doc_8831", "title": "五一营销活动方案 v3", "scope": "department" } ],\n'
'    "tool_calls": [ { "tool": "rag_retrieve", "authorized": true } ]\n'
'  }\n'
'}', S))
e.append(Spacer(1, 3*mm))

# 17.6.4
e.append(Paragraph("17.6.4 审批 API（入驻 / 共享 / 导出）", S["h3"]))
e.append(Paragraph("POST /api/v1/approval/{approval_id}/decide —— 仅平台管理员 / 部门管理员 / 授权人可审批，记录操作人。", S["body"]))
e.append(Spacer(1, 1.5*mm))
d54 = [[Hcell("请求字段"), Hcell("类型"), Hcell("必填"), Hcell("说明")],
       [Paragraph("decision", S["code"]), Paragraph("enum", S["code"]), Paragraph("是", S["body"]), Paragraph("approve / reject", S["body"])],
       [Paragraph("comment", S["code"]), Paragraph("string", S["code"]), Paragraph("否", S["body"]), Paragraph("审批意见", S["body"])],
       [Paragraph("operator", S["code"]), Paragraph("object", S["code"]), Paragraph("是", S["body"]), Paragraph("网关注入审批人身份，校验其审批资格", S["body"])]]
e.append(build_table(d54, [30*mm, 22*mm, 14*mm, 112*mm], S))
e.append(Spacer(1, 1.5*mm))
e.append(Paragraph("响应示例：", S["lbl"]))
e.append(code_block(
'{\n'
'  "code": 0,\n'
'  "data": {\n'
'    "approval_id": "ap_5502",\n'
'    "status": "approved",\n'
'    "decided_by": "u_admin_01",\n'
'    "decided_at": "2026-05-01T09:12:00Z",\n'
'    "audit_id": "audit_77"\n'
'  }\n'
'}', S))
e.append(Spacer(1, 3*mm))

# 17.6.5
e.append(Paragraph("17.6.5 知识读写（个人 / 部门）", S["h3"]))
e.append(Paragraph("POST /api/v1/kb/documents —— 受 RBAC 与 scope 约束；写入时强制回填 owner_id / dept_id / scope 元数据。", S["body"]))
e.append(Spacer(1, 1.5*mm))
d55 = [[Hcell("请求字段"), Hcell("类型"), Hcell("必填"), Hcell("说明")],
       [Paragraph("title", S["code"]), Paragraph("string", S["code"]), Paragraph("是", S["body"]), Paragraph("文档标题", S["body"])],
       [Paragraph("content", S["code"]), Paragraph("text", S["code"]), Paragraph("是", S["body"]), Paragraph("正文（入库自动向量化）", S["body"])],
       [Paragraph("scope", S["code"]), Paragraph("enum", S["code"]), Paragraph("是", S["body"]), Paragraph("personal / department / global（global 仅平台管理员）", S["body"])],
       [Paragraph("dept_id", S["code"]), Paragraph("string", S["code"]), Paragraph("条件", S["body"]), Paragraph("scope=department 时必填，强制等于 caller.deptId（不可越权写他部）", S["body"])],
       [Paragraph("caller", S["code"]), Paragraph("object", S["code"]), Paragraph("是", S["body"]), Paragraph("网关注入，服务端校验写权限", S["body"])]]
e.append(build_table(d55, [28*mm, 20*mm, 16*mm, 114*mm], S))
e.append(Spacer(1, 1.5*mm))
e.append(Paragraph("响应示例：", S["lbl"]))
e.append(code_block(
'{\n'
'  "code": 0,\n'
'  "data": {\n'
'    "doc_id": "doc_9001",\n'
'    "scope": "department",\n'
'    "owner_id": "u_1024",\n'
'    "dept_id": "d_sales",\n'
'    "indexed": true\n'
'  }\n'
'}', S))
e.append(Spacer(1, 2*mm))
e.append(Paragraph(
    "注：所有写接口的 scope / dept_id 以服务端 caller 为准，前端传入仅作展示，防止越权写库。",
    S["note"]))

doc.build(e)
print("SAVED:", os.path.join(BASE, "移动端MVP开发清单_A4.pdf"))
