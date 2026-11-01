# -*- coding: utf-8 -*-
"""单独导出「第 16 章 移动端（手机）操作设计」为带线框图的 A4 PDF，便于给产品/技术评审。
中文：msyh.ttc(常规) / msyhbd.ttc(粗体)；线框图复用 html_to_docx.render_svg 渲染。
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
CARDBG = colors.HexColor("#eef4fb")

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

# ---- 提取并渲染线框图（HTML 中第 4 个 svg 块，index 3）----
html_text = open(os.path.join(BASE, "企业AI智能体中台蓝图.html"), encoding="utf-8").read()
blocks = re.findall(r"<svg\b.*?</svg>", html_text, re.S)
wire_png = os.path.join(BASE, "_mobile_wireframe.png")
H.render_svg(blocks[3], wire_png)
print("wireframe rendered ->", wire_png)

doc = SimpleDocTemplate(os.path.join(BASE, "移动端设计_A4.pdf"), pagesize=A4,
                        leftMargin=16*mm, rightMargin=16*mm,
                        topMargin=15*mm, bottomMargin=15*mm,
                        title="移动端（手机）操作设计")
e = []
e.append(Paragraph("移动端（手机）操作设计", S["title"]))
e.append(Paragraph("企业 AI 智能体中台 · 第 16 章 · 给产品 / 技术评审（A4 单页）", S["sub"]))
e.append(Spacer(1, 5*mm))

e.append(Paragraph(
    "平台不止服务于坐班 PC 场景。管理层在外、销售/跟单在外勤、员工随时提问、审批待办都发生在手机上。"
    "移动端设计目标：<b>高频轻操作手机化、重编排留 PC、权限与安全同标准</b>。", S["intro"]))
e.append(Spacer(1, 3*mm))

# 16.1
e.append(Paragraph("16.1 三种接入方式对比", S["h2"]))
d1 = [[Hcell("接入方式"), Hcell("适用"), Hcell("开发成本"), Hcell("SSO/通知"), Hcell("推荐度")],
      [Paragraph("企微/钉钉/飞书 H5 / 小程序", S["body"]),
       Paragraph("全员日常使用、免安装、与 IM 通知打通", S["body"]),
       Paragraph("低（复用 Web App + 网页授权）", S["body"]),
       Paragraph("天然打通", S["body"]),
       Paragraph("<b>首选（MVP）</b>", S["body"])],
      [Paragraph("独立 App（Flutter/RN）", S["body"]),
       Paragraph("功能最全、可离线缓存、强推送", S["body"]),
       Paragraph("中高", S["body"]),
       Paragraph("需自接 SSO", S["body"]),
       Paragraph("二期", S["body"])],
      [Paragraph("微信小程序", S["body"]),
       Paragraph("外部协作/客户场景、免登录门槛", S["body"]),
       Paragraph("低", S["body"]),
       Paragraph("微信授权", S["body"]),
       Paragraph("对外场景", S["body"])]]
e.append(build_table(d1, [33*mm, 38*mm, 33*mm, 22*mm, 20*mm], S))
e.append(Spacer(1, 3*mm))

# 16.2
e.append(Paragraph("16.2 手机上做什么 / 不做什么（场景分级）", S["h2"]))
d2 = [[Hcell("层级"), Hcell("手机端（推荐）"), Hcell("PC 端（保留）")],
      [Paragraph("<b>问答与触发</b>", S["body"]),
       Paragraph("语音/文字问知识、一句话触发 Agent、看结果卡片", S["body"]),
       Paragraph("复杂多轮调试", S["body"])],
      [Paragraph("<b>审批与待办</b>", S["body"]),
       Paragraph("部门入驻/共享/导出申请一键通过驳回", S["body"]),
       Paragraph("权限策略配置", S["body"])],
      [Paragraph("<b>知识检索</b>", S["body"]),
       Paragraph("搜制度/产品/案例，带权限标识的结果", S["body"]),
       Paragraph("批量入库打标", S["body"])],
      [Paragraph("<b>生产</b>", S["body"]),
       Paragraph("即时生成短文案/日报/纪要", S["body"]),
       Paragraph("长文档/报表/工作流编排", S["body"])],
      [Paragraph("<b>管理</b>", S["body"]),
       Paragraph("看用量看板、接收告警", S["body"]),
       Paragraph("部门入驻、RBAC、模型网关", S["body"])]]
e.append(build_table(d2, [26*mm, 70*mm, 50*mm], S))
e.append(Spacer(1, 3*mm))

# 16.3 wireframe
e.append(Paragraph("16.3 核心界面设计（手机线框图）", S["h2"]))
e.append(Paragraph(
    "三块核心屏：① 工作台首页（快捷入口+待办）　② Agent 对话页（语音优先+引用来源）　③ 审批页（一键通过/驳回）。",
    S["intro"]))
e.append(Spacer(1, 2*mm))
e.append(Image(wire_png, width=150*mm, height=150*mm*360/680))
e.append(Spacer(1, 3*mm))

# 16.4
e.append(Paragraph("16.4 交互设计原则", S["h2"]))
for b in bullets([
    "语音优先：移动端默认语音输入（ASR），降低打字成本，适合外勤/驾驶场景",
    "一句话触发 Agent：自然语言指令直接映射到工作流（如“生成今日跟单日报”）",
    "卡片化结果：答案以结构化卡片 + 引用来源呈现，便于小屏阅读与转发",
    "权限可视化：每条结果标注可见范围（全域/部门/个人），避免越权困惑",
    "轻审批流：入驻/共享/导出等关键操作手机一键审批，PC 留痕",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 16.5
e.append(Paragraph("16.5 移动安全", S["h2"]))
for b in bullets([
    "SSO + 设备绑定：首次登录绑定设备，换设备需二次验证",
    "水印：敏感页面叠加“姓名+时间”水印，震慑截屏外传",
    "远程注销：离职/丢机可在后台一键擦除会话与设备登录态",
    "防截屏/复制：高敏部门（财务/HR）可选禁止截屏与复制",
    "越权防护同 PC：召回层按 scope/owner/dept 过滤，移动端不绕过",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 16.6
e.append(Paragraph("16.6 弱网与离线", S["h2"]))
for b in bullets([
    "预缓存：部门制度/产品手册等常用知识本地预取，弱网可查",
    "断点续传：对话与任务提交在网络恢复后自动续传",
    "不存明文敏感：本地仅存脱敏摘要，敏感内容随会话结束清除",
], S):
    e.append(b)
e.append(Spacer(1, 3*mm))

# 16.7
e.append(Paragraph("16.7 实施建议", S["h2"]))
e.append(Paragraph(
    "MVP 阶段先以<b>企微/钉钉/飞书 H5</b> 嵌入（复用 Dify Web App，开发量最小、SSO 与通知天然打通），"
    "验证手机问答+审批闭环；二期再做<b>独立 App</b>（Flutter）补齐离线缓存与强推送；对外协作场景用<b>微信小程序</b>。"
    "所有移动端共享同一套权限与审计，不另起炉灶。", S["intro"]))
e.append(Spacer(1, 2*mm))
e.append(Paragraph(
    "配套参考：第 17 章《移动端 MVP 开发清单》（页面/接口/权限点/排期）、第 18 章《PC 与移动端权限一致性》。",
    S["note"]))

doc.build(e)
print("SAVED:", os.path.join(BASE, "移动端设计_A4.pdf"))
