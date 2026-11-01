# -*- coding: utf-8 -*-
"""启动期智能体配置 + 平台自动演化  ->  A4 评审包附册 PDF"""
import os, re, io, math
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image as PILImage, ImageDraw, ImageFont

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
OUT = os.path.join(BASE, "启动与演化_A4.pdf")

pdfmetrics.registerFont(TTFont("YaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHei-Bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0))
FONT_PATHS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\simsun.ttc"]

NAVY = colors.HexColor("#0a2540")
BLUE = colors.HexColor("#1565c0")
RED = colors.HexColor("#c62828")
ORANGE = colors.HexColor("#ef6c00")
GREEN = colors.HexColor("#2e7d32")
LGRAY = colors.HexColor("#f5f8fc")
LINEC = colors.HexColor("#dbe6f2")
INK = colors.HexColor("#1f2d3d")
MUT = colors.HexColor("#5b7185")

styles = {
    'title': ParagraphStyle('t', fontName='YaHei-Bold', fontSize=17, textColor=NAVY, spaceAfter=2, leading=21),
    'sub': ParagraphStyle('s', fontName='YaHei', fontSize=9.5, textColor=MUT, spaceAfter=6),
    'h2': ParagraphStyle('h2', fontName='YaHei-Bold', fontSize=13.5, textColor=NAVY, spaceBefore=10, spaceAfter=4, leading=17),
    'h3': ParagraphStyle('h3', fontName='YaHei-Bold', fontSize=11, textColor=BLUE, spaceBefore=7, spaceAfter=3, leading=14),
    'body': ParagraphStyle('b', fontName='YaHei', fontSize=9, textColor=INK, leading=13, spaceAfter=3),
    'small': ParagraphStyle('sm', fontName='YaHei', fontSize=8, textColor=MUT, leading=11),
    'cell': ParagraphStyle('c', fontName='YaHei', fontSize=8, textColor=INK, leading=11),
    'cellh': ParagraphStyle('ch', fontName='YaHei-Bold', fontSize=8, textColor=colors.white, leading=11),
    'note': ParagraphStyle('n', fontName='YaHei', fontSize=8.5, textColor=colors.HexColor('#7a5b00'), leading=12),
    'bullet': ParagraphStyle('bu', fontName='YaHei', fontSize=9, textColor=INK, leading=13, leftIndent=10, spaceAfter=2),
}

def make_table(header, rows, widths):
    data = [[Paragraph(h, styles['cellh']) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c), styles['cell']) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('GRID', (0, 0), (-1, -1), 0.5, LINEC),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LGRAY]),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t

# ---------- SVG -> PNG (subset, rect/line/text only) ----------
def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def parse_attrs(s):
    d = {}
    for k, v in re.findall(r'(\w[\w-]*)\s*=\s*"([^"]*)"', s):
        d[k] = v
    return d

_SVG_FONT = None
def get_font(sz):
    global _SVG_FONT
    if _SVG_FONT is None:
        for p in FONT_PATHS:
            if os.path.exists(p):
                _SVG_FONT = p
                break
    try:
        return ImageFont.truetype(_SVG_FONT, int(sz)) if _SVG_FONT else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def render_svg(svg, out_png, scale=2):
    vb = re.search(r'viewBox="([\d.\s]+)"', svg)
    nums = [float(x) for x in vb.group(1).split()]
    vw, vh = nums[2], nums[3]
    W, H = int(vw * scale), int(vh * scale)
    img = PILImage.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    for m in re.finditer(r'<rect\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x = float(a.get('x', 0)) * scale; y = float(a.get('y', 0)) * scale
        w = float(a.get('width', 0)) * scale; h = float(a.get('height', 0)) * scale
        rx = float(a.get('rx', 0)) * scale
        fill = a.get('fill', 'none'); stroke = a.get('stroke')
        sw = float(a.get('stroke-width', 1)) * scale
        fcol = hex2rgb(fill) if fill.startswith('#') else None
        scol = hex2rgb(stroke) if (stroke and stroke.startswith('#')) else None
        try:
            d.rounded_rectangle([x, y, x + w, y + h], radius=rx, fill=fcol, outline=scol,
                                width=(int(sw) if scol else 0))
        except Exception:
            d.rectangle([x, y, x + w, y + h], fill=fcol, outline=scol, width=(int(sw) if scol else 0))
    for m in re.finditer(r'<line\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x1 = float(a['x1']) * scale; y1 = float(a['y1']) * scale
        x2 = float(a['x2']) * scale; y2 = float(a['y2']) * scale
        stroke = a.get('stroke', '#000'); sw = float(a.get('stroke-width', 1)) * scale
        col = hex2rgb(stroke) if stroke.startswith('#') else (0, 0, 0)
        ds = a.get('stroke-dasharray')
        d.line([(x1, y1), (x2, y2)], fill=col, width=max(1, int(sw)))
        if 'marker-end' in a:
            ang = math.atan2(y2 - y1, x2 - x1); L = 11 * scale
            a1 = ang + math.radians(150); a2 = ang - math.radians(150)
            p1 = (x2 + L * math.cos(a1), y2 + L * math.sin(a1))
            p2 = (x2 + L * math.cos(a2), y2 + L * math.sin(a2))
            d.line([(x2, y2), p1], fill=col, width=max(1, int(sw)))
            d.line([(x2, y2), p2], fill=col, width=max(1, int(sw)))
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', svg, re.S):
        a = parse_attrs(m.group(1))
        content = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not content:
            continue
        x = float(a.get('x', 0)) * scale; y = float(a.get('y', 0)) * scale
        sz = float(a.get('font-size', 12)); fill = a.get('fill', '#000')
        col = hex2rgb(fill) if fill.startswith('#') else (0, 0, 0)
        anc = a.get('text-anchor', 'start')
        fnt = get_font(sz)
        d.text((x, y), content, font=fnt, fill=col, anchor='mm' if anc == 'middle' else 'la')
    img.save(out_png, 'PNG')
    return out_png

LOOP_SVG = '''<svg viewBox="0 0 680 200" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="平台自动演化闭环总图">
  <defs>
    <marker id="me1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker>
    <marker id="me2" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#ef6c00"/></marker>
  </defs>
  <rect x="20" y="30" width="100" height="56" rx="8" fill="#0a2540"/>
  <text x="70" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">① 观测采集</text>
  <text x="70" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">埋点 + 用户反馈</text>
  <rect x="152" y="30" width="100" height="56" rx="8" fill="#0a2540"/>
  <text x="202" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">② 评估</text>
  <text x="202" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">质量/覆盖/漂移</text>
  <rect x="284" y="30" width="100" height="56" rx="8" fill="#0a2540"/>
  <text x="334" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">③ 提议</text>
  <text x="334" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">自动改进建议</text>
  <rect x="416" y="30" width="100" height="56" rx="8" fill="#0a2540"/>
  <text x="466" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">④ 审批</text>
  <text x="466" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">人在环确认</text>
  <rect x="548" y="30" width="100" height="56" rx="8" fill="#0a2540"/>
  <text x="598" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">⑤ 应用</text>
  <text x="598" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">灰度 + 生效</text>
  <line x1="120" y1="58" x2="150" y2="58" stroke="#1565c0" stroke-width="2" marker-end="url(#me1)"/>
  <line x1="252" y1="58" x2="282" y2="58" stroke="#1565c0" stroke-width="2" marker-end="url(#me1)"/>
  <line x1="384" y1="58" x2="414" y2="58" stroke="#1565c0" stroke-width="2" marker-end="url(#me1)"/>
  <line x1="516" y1="58" x2="546" y2="58" stroke="#1565c0" stroke-width="2" marker-end="url(#me1)"/>
  <line x1="598" y1="86" x2="598" y2="150" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4"/>
  <line x1="598" y1="150" x2="72" y2="150" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4"/>
  <line x1="72" y1="150" x2="72" y2="88" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#me2)"/>
  <text x="340" y="138" text-anchor="middle" font-size="10" font-weight="700" fill="#ef6c00">持续闭环：应用后回流观测，进入下一轮自动评估</text>
</svg>'''

# ---------- content ----------
P0 = '<font color="#c62828"><b>P0</b></font>'
P1 = '<font color="#ef6c00"><b>P1</b></font>'

L1 = [
    ("平台向导 Agent（HelpDesk）", "回答怎么用平台、导航到正确智能体、解释权限模型", "知识库问答 RAG + 路由", P0),
    ("业务梳理教练 Agent", "交互式引导拆解业务流程，输出智能体草图（步骤/知识/工具/权限）", "多轮对话+表单采集+提示词模板", P0),
    ("部门模板中心", "3–5 个开箱即用部门模板（HR/销售/客服…），一键克隆", "模板市场+权限配置器", P0),
    ("安全合规哨兵 Agent", "提示数据边界、拦截越权、做合规问答", "权限校验+RAG", P1),
    ("最佳实践推荐 Agent", "给任务推荐现成智能体/模板，避免重复造", "检索+推荐", P1),
]
L2 = [
    ("可视化 Agent 编排器", "零代码搭智能体：人设/知识/工具/变量", "Dify Agent/Studio", P0),
    ("业务梳理画布", "业务拆步骤，每步映射节点或子智能体，导出草稿", "画布+模板", P0),
    ("知识接入连接器", "文档/库/文件夹同步，自动切片向量化打标签", "连接器+Qdrant+PG", P0),
    ("工具/插件注册中心", "内部 API/函数注册为可调用工具（鉴权限流）", "Tool Registry/One API", P0),
    ("角色与可见范围配置器", "设 owner/dept/scope，绑 RBAC，确保不越权", "权限元数据+召回过滤", P0),
    ("提示词模板与变量库", "可复用提示块+变量，降门槛", "Prompt 模板", P1),
    ("试运行与评估台", "发布前测试、对比、留痕", "Sandbox/Eval", P1),
]
L3 = [
    ("工作流编排器", "拖拽节点串多智能体/工具，可视化调试", "Dify Workflow/LangGraph", P0),
    ("节点类型库", "触发器/条件/循环/并行/LLM/代码/检索/HTTP", "节点引擎", P0),
    ("Agent-to-Agent 调用", "一个智能体作节点调另一个，分工协作", "A2A 协议", P0),
    ("人工审核节点", "敏感步骤加审批闸口（HITL）", "审批 API", P0),
    ("事件/消息总线", "解耦智能体、异步交接、长流程", "消息总线", P1),
    ("定时调度器", "按 cron 周期跑（日报/巡检/对账）", "Scheduler", P1),
    ("执行监控与回放", "看链路、调试、回溯", "Trace/观测", P1),
]
ROAD = [
    ("W1", "底座", "私有化部署 Dify+Qdrant+PG+MinIO+One API，配三级知识库与 RBAC，打通 SSO"),
    ("W2", "教学层", "预置 L1 三个 P0（向导/教练/模板中心），写平台手册知识库"),
    ("W3", "构建层", "开放 L2 工具，选 1 试点部门（建议行政部）做 2–3 业务智能体"),
    ("W4", "编排层", "用 L3 编成 1 条端到端工作流，复盘沉淀为模板推广"),
]
MAP = [
    ("三级知识库、权限范围", "第 5 章", "构建层知识接入/权限配置器直接落到此"),
    ("RBAC 三角色、可见范围", "第 13 章", "员工建智能体时绑定这套角色"),
    ("单一过滤点、PC/移动一致", "第 16–18 章", "所有智能体/工作流共用召回过滤"),
    ("接口契约", "openapi.yaml", "工具注册中心、A2A 调用基于这些接口"),
]
DASH = [
    ("回答一次解决率", "无需追问/纠错的占比", "低于阈值→触发提示词 A/B"),
    ("知识覆盖率", "问题可命中权威知识的比例", "低→建议补文档/接新来源"),
    ("越权拦截率", "过滤点拦下的越权尝试", "异常升高→排查配置/攻击"),
    ("智能体健康分", "各智能体综合评分", "低分→标待优化推负责人"),
    ("待审建议数", "演化建议台积压", "积压→提醒管理员处理"),
]
MILE = [
    ("P0", "埋点+反馈+建议台+审批", "演化数据底座与人在环闸门，先让改进可见可控"),
    ("P1", "知识缺口探测+智能体评估", "自动发现该补的知识、该优化的智能体"),
    ("P2", "提示词 A/B+工作流自演化+灰度回滚", "半自动优化闭环，规模化自我进步"),
]

W = A4[0] - 2 * 16 * mm

def build():
    E = []
    E.append(Paragraph("启动期智能体配置 + 平台自动演化", styles['title']))
    E.append(Paragraph("企业 AI 智能体中台 · 评审包附册 · 通用版 · v1.9 · 2026-10-31", styles['sub']))

    # ---- Chapter 19 ----
    E.append(Paragraph("19. 启动期智能体与工具配置清单", styles['h2']))
    E.append(Paragraph(
        "<b>核心结论：</b>启动期按“教学 → 构建 → 编排”三层递进配置。L1 预置示范智能体帮员工上手；"
        "L2 提供业务转智能体工具让员工把部门业务变成智能体；L3 提供多智能体工作流工具把多个智能体串成自动化流程。"
        "L2 是承上启下的关键，L3 复用 L1/L2 产物并闭环沉淀回模板库。", styles['body']))

    E.append(Paragraph("19.1 L1 教学层：预置示范智能体（帮员工学习使用）", styles['h3']))
    E.append(Paragraph("由平台管理员在启动期预置，员工首次进入即可“有向导、有教练、有模板可抄”。", styles['body']))
    E.append(make_table(["预置智能体", "主要作用", "基于平台能力", "优先级"],
                        L1, [88, 224, 128, 50]))

    E.append(Paragraph("19.2 L2 构建层：业务转智能体工具（让员工把公司业务整理成智能体）", styles['h3']))
    E.append(Paragraph("核心是“四件套 + 权限配置”，员工零代码即可完成。业务梳理画布直接对接 L1 教练产出的草图。", styles['body']))
    E.append(make_table(["工具", "作用", "对应平台能力", "优先级"],
                        L2, [92, 220, 124, 50]))

    E.append(Paragraph("19.3 L3 编排层：多智能体工作流工具（把各智能体组成工作流程）", styles['h3']))
    E.append(Paragraph("单个智能体解决单点任务，工作流把它们串成端到端业务。核心是“编排器 + 节点库 + A2A 调用 + 人工审核”。", styles['body']))
    E.append(make_table(["工具", "作用", "对应平台能力", "优先级"],
                        L3, [92, 220, 124, 50]))

    E.append(Paragraph("19.4 启动期 4 周落地路线图", styles['h3']))
    E.append(make_table(["周次", "主题", "关键动作"], ROAD, [46, 78, 368]))

    E.append(Paragraph("19.5 与既有蓝图章节对应关系", styles['h3']))
    E.append(make_table(["本清单内容", "蓝图对应章节", "说明"], MAP, [130, 96, 266]))

    E.append(PageBreak())

    # ---- Chapter 20 ----
    E.append(Paragraph("20. 平台自动演化与自我进步机制", styles['h2']))
    E.append(Paragraph(
        "<b>核心结论：</b>平台不应“上线即定型”，而应内置<b>观测 → 评估 → 提议 → 审批 → 应用</b>的自动演化闭环，"
        "让智能体、知识库、工作流随真实使用持续变好。关键约束：<b>一切演化都不绕过第 18 章的单一过滤点</b>，"
        "自动产出的知识/智能体仍受权限元数据约束，且所有自动建议必须经“人在环审批”后生效、可灰度、可回滚。", styles['body']))

    E.append(Paragraph("20.1 演化闭环总图", styles['h3']))
    E.append(Paragraph("每一次问答、纠错、使用数据都回流到演化引擎；引擎评估后产出改进建议，经审批、灰度发布后生效，再回到观测——形成自我进步飞轮。", styles['body']))
    png = render_svg(LOOP_SVG, os.path.join(BASE, "_loop.png"), scale=2)
    im = PILImage.open(png); ratio = im.size[0] / im.size[1]
    img_w = 460
    E.append(Image(png, width=img_w, height=img_w / ratio))
    E.append(Paragraph("▲ 图示：平台自动演化闭环（单一过滤点不被绕过）", styles['small']))

    E.append(Paragraph("20.2 六大自演化机制", styles['h3']))
    E.append(Paragraph("20.2.1 全链路埋点与反馈采集", styles['h3']))
    E.append(Paragraph("每个智能体调用都记录：输入、输出、引用来源、耗时、是否命中过滤、用户评分（赞/踩）与自由文本纠错（“答错了/答不全/应引用某文档”）。这是演化的数据底座。", styles['body']))
    E.append(Paragraph("20.2.2 知识缺口探测与自生长", styles['h3']))
    for t in ["当某类问题频繁“未找到/低置信”，自动标记知识缺口并建议补充文档",
              "对已接入来源（文件夹/数据库）按计划重新抓取，刷新过期向量，避免知识 stale",
              "新增文档自动继承来源 owner/dept/scope，不破坏分级权限"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("20.2.3 智能体效果评估与自优化", styles['h3']))
    for t in ["按智能体统计：一次解决率、幻觉率、兜底率、被纠错率",
              "对提示词做自动 A/B：候选变体小流量对比，胜出者进提议队列",
              "长期低分的智能体自动打标“待优化”，推给负责人"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("20.2.4 工作流瓶颈与自演化", styles['h3']))
    for t in ["识别耗时异常/失败率高的节点，建议加人工审核或并行",
              "某节点失败时可建议降级/旁路策略（保留人工兜底）",
              "高频固定流程可建议固化为模板，反哺 L1 模板中心"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("20.2.5 演化建议台与人在环审批", styles['h3']))
    E.append(Paragraph("安全底线：任何自动提议默认不直灌生产。所有改进（改提示、加知识、调工作流）先进入“演化建议台”，由平台/部门管理员确认后才生效——既享受自动演化红利，又守住合规与可控。", styles['note']))
    E.append(Paragraph("20.2.6 版本化、灰度与回滚", styles['h3']))
    for t in ["每一次生效的改动都版本化，一键回滚到上一版",
              "新版本先对单个部门灰度，无异常再全量",
              "演化动作本身进入统一审计流，可追溯到“谁审批、何时生效”"]:
        E.append(Paragraph("• " + t, styles['bullet']))

    E.append(Paragraph("20.3 演化仪表盘（核心指标）", styles['h3']))
    E.append(make_table(["指标", "含义", "演化动作示例"], DASH, [110, 200, 180]))

    E.append(Paragraph("20.4 权限守恒：演化不绕过单一过滤点", styles['h3']))
    E.append(Paragraph("自动演化产出的任何知识、智能体、工作流，在读取与生成时仍走第 18 章的同一召回过滤；新增知识自动继承 scope/owner/dept；“自动写库”接口（openapi.yaml 的 /kb/documents）由服务端强制回填权限元数据，前端/演化引擎均不可伪造。权限模型不会因“系统自动”而放松。", styles['body']))

    E.append(Paragraph("20.5 落地优先级与里程碑", styles['h3']))
    E.append(make_table(["阶段", "范围", "产出"], MILE, [50, 170, 270]))

    return E

def main():
    doc = SimpleDocTemplate(OUT, pagesize=A4,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title="启动期智能体配置 + 平台自动演化")
    doc.build(build())
    print("SAVED:", OUT)

if __name__ == '__main__':
    main()
