# -*- coding: utf-8 -*-
"""单独导出「第 18 章 PC 与移动端权限一致性」为 A4 PDF（含 18.4 数据流对照图），
强调单一过滤点。中文：msyh.ttc / msyhbd.ttc；SVG 复用 html_to_docx.render_svg 渲染。
"""
import os, re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
import html_to_docx as H

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

def styles():
    return {
        "title": ParagraphStyle("title", fontName="YaHei-Bold", fontSize=17,
                                textColor=NAVY, alignment=TA_CENTER, leading=21),
        "sub": ParagraphStyle("sub", fontName="YaHei", fontSize=9.5,
                              textColor=MUTED, alignment=TA_CENTER, leading=13),
        "h2": ParagraphStyle("h2", fontName="YaHei-Bold", fontSize=12.5,
                             textColor=NAVY, leading=16, spaceBefore=8),
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

def bullets(items, S):
    return [Paragraph("• " + x, S["bullet"]) for x in items]

S = styles()

# ---- 提取 18.4 数据流对照图（按 aria-label 定位 SVG 块）----
html_text = open(os.path.join(BASE, "企业AI智能体中台蓝图.html"), encoding="utf-8").read()
blocks = re.findall(r"<svg\b.*?</svg>", html_text, re.S)
target = None
for b in blocks:
    if "PC与移动端共用召回过滤" in b:
        target = b
        break
flow_png = os.path.join(BASE, "_ch18_flow.png")
if target:
    H.render_svg(target, flow_png)
    print("flow diagram rendered ->", flow_png)
else:
    print("WARN: 未找到 18.4 SVG 块")

doc = SimpleDocTemplate(os.path.join(BASE, "PC与移动端权限一致性_A4.pdf"), pagesize=A4,
                        leftMargin=16*mm, rightMargin=16*mm,
                        topMargin=15*mm, bottomMargin=15*mm,
                        title="PC 与移动端权限一致性")
e = []
e.append(Paragraph("PC 与移动端权限一致性", S["title"]))
e.append(Paragraph("企业 AI 智能体中台 · 第 18 章 · 给技术 / 安全评审（A4）", S["sub"]))
e.append(Spacer(1, 5*mm))

e.append(Paragraph(
    "平台无论通过 PC Web 还是手机 H5/App 访问，<b>权限判定只用同一套逻辑、同一份数据</b>，"
    "绝不因入口不同而放宽或收紧。这是避免“手机端泄密”“两端看到的不一样”等问题的根本保障。", S["intro"]))
e.append(Spacer(1, 3*mm))

# 18.1
e.append(Paragraph("18.1 共用同一召回过滤层", S["h2"]))
e.append(Paragraph(
    "PC 与移动端都调用<b>同一个 RAG 召回服务</b>：请求携带调用者身份（userId / deptId / role），"
    "服务在<b>召回阶段</b>即按 scope / owner / dept 过滤，越权片段根本不进入大模型上下文。"
    "两端没有任何“旁路”——手机端不是只读镜像，而是同一服务的不同前端。", S["intro"]))
e.append(Spacer(1, 2*mm))
for b in bullets([
    "同一过滤代码：过滤逻辑只在召回服务实现一次，PC/移动共用",
    "同一元数据：每条知识带 (owner_id, dept_id, scope)，两端一致",
    "同一 RBAC：角色与策略在服务端校验，前端仅做展示",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 18.2
e.append(Paragraph("18.2 一致性检查清单", S["h2"]))
d2 = [[Hcell("检查项"), Hcell("PC 端"), Hcell("移动端"), Hcell("判定")],
      [Paragraph("可见部门空间", S["body"]), Paragraph("仅所属部门", S["body"]), Paragraph("仅所属部门", S["body"]), Paragraph("应一致", S["body"])],
      [Paragraph("可见知识范围", S["body"]), Paragraph("全域+部门+个人", S["body"]), Paragraph("全域+部门+个人", S["body"]), Paragraph("应一致", S["body"])],
      [Paragraph("越权访问测试", S["body"]), Paragraph("不可见对方部门", S["body"]), Paragraph("不可见对方部门", S["body"]), Paragraph("应一致", S["body"])],
      [Paragraph("审批资格", S["body"]), Paragraph("按角色", S["body"]), Paragraph("按角色", S["body"]), Paragraph("应一致", S["body"])],
      [Paragraph("审计留痕", S["body"]), Paragraph("全量记录", S["body"]), Paragraph("全量记录", S["body"]), Paragraph("应一致", S["body"])],
      [Paragraph("导出审批", S["body"]), Paragraph("敏感走审批", S["body"]), Paragraph("敏感走审批", S["body"]), Paragraph("应一致", S["body"])]]
e.append(build_table(d2, [34*mm, 48*mm, 48*mm, 28*mm], S))
e.append(Spacer(1, 3*mm))

# 18.3
e.append(Paragraph("18.3 风险与纠偏", S["h2"]))
for b in bullets([
    "风险：移动端为赶进度“临时放宽”过滤，或前端缓存越权内容",
    "纠偏：过滤器只在服务端，前端不持有越权数据；缓存仅存脱敏摘要",
    "测试：两端共用同一套越权测试用例，CI 中强制回归",
    "审计：两端操作统一进入同一审计流，出现差异可追溯到具体入口",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 18.4
e.append(Paragraph("18.4 数据流对照图：PC 与移动端共用召回过滤（单一过滤点）", S["h2"]))
e.append(Paragraph(
    "下图强调：PC Web 与手机 H5/App 是<b>同一个召回服务的两个前端</b>，不存在“移动端只读镜像”或“旁路”。"
    "所有权限判定集中在<b>唯一过滤点</b>（scope / owner / dept），越权片段在召回阶段即被剔除，"
    "根本不进入大模型上下文。", S["intro"]))
e.append(Spacer(1, 2*mm))
if os.path.exists(flow_png):
    e.append(Image(flow_png, width=160*mm, height=160*mm*410/680))
    e.append(Spacer(1, 2*mm))
e.append(Paragraph(
    "配套参考：第 16 章《移动端（手机）操作设计》、第 17 章《移动端 MVP 开发清单》（含接口字段级定义）。"
    "三份 A4 合并即“移动端评审包”。", S["note"]))

doc.build(e)
print("SAVED:", os.path.join(BASE, "PC与移动端权限一致性_A4.pdf"))
