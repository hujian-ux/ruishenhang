# -*- coding: utf-8 -*-
"""第25章（大模型接入与计量计费）-> 评审包附册 A4 PDF"""
import os, re, math
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
OUT = os.path.join(BASE, "大模型接入与计量计费_A4.pdf")

pdfmetrics.registerFont(TTFont("YaHei", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHei-Bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0))
FONT_PATHS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]

NAVY = colors.HexColor("#0a2540"); BLUE = colors.HexColor("#1565c0")
RED = colors.HexColor("#c62828"); ORANGE = colors.HexColor("#ef6c00")
GREEN = colors.HexColor("#2e7d32"); LGRAY = colors.HexColor("#f5f8fc")
LINEC = colors.HexColor("#dbe6f2"); INK = colors.HexColor("#1f2d3d"); MUT = colors.HexColor("#5b7185")

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

def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3: h = ''.join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
def parse_attrs(s):
    return {k: v for k, v in re.findall(r'(\w[\w-]*)\s*=\s*"([^"]*)"', s)}
_FONT = None
def get_font(sz):
    global _FONT
    if _FONT is None:
        for p in FONT_PATHS:
            if os.path.exists(p): _FONT = p; break
    try: return ImageFont.truetype(_FONT, int(sz)) if _FONT else ImageFont.load_default()
    except Exception: return ImageFont.load_default()
def render_svg(svg, out_png, scale=2):
    vb = re.search(r'viewBox="([\d.\s]+)"', svg); nums = [float(x) for x in vb.group(1).split()]
    vw, vh = nums[2], nums[3]; W, H = int(vw*scale), int(vh*scale)
    img = PILImage.new('RGB', (W, H), 'white'); d = ImageDraw.Draw(img)
    for m in re.finditer(r'<rect\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x=float(a.get('x',0))*scale; y=float(a.get('y',0))*scale
        w=float(a.get('width',0))*scale; h=float(a.get('height',0))*scale
        rx=float(a.get('rx',0))*scale; fill=a.get('fill','none'); stroke=a.get('stroke')
        sw=float(a.get('stroke-width',1))*scale
        fcol=hex2rgb(fill) if fill.startswith('#') else None
        scol=hex2rgb(stroke) if (stroke and stroke.startswith('#')) else None
        try: d.rounded_rectangle([x,y,x+w,y+h], radius=rx, fill=fcol, outline=scol, width=(int(sw) if scol else 0))
        except Exception: d.rectangle([x,y,x+w,y+h], fill=fcol, outline=scol, width=(int(sw) if scol else 0))
    for m in re.finditer(r'<line\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x1=float(a['x1'])*scale; y1=float(a['y1'])*scale; x2=float(a['x2'])*scale; y2=float(a['y2'])*scale
        stroke=a.get('stroke','#000'); sw=float(a.get('stroke-width',1))*scale
        col=hex2rgb(stroke) if stroke.startswith('#') else (0,0,0)
        d.line([(x1,y1),(x2,y2)], fill=col, width=max(1,int(sw)))
        if 'marker-end' in a:
            ang=math.atan2(y2-y1,x2-x1); L=11*scale
            a1=ang+math.radians(150); a2=ang-math.radians(150)
            d.line([(x2,y2),(x2+L*math.cos(a1),y2+L*math.sin(a1))], fill=col, width=max(1,int(sw)))
            d.line([(x2,y2),(x2+L*math.cos(a2),y2+L*math.sin(a2))], fill=col, width=max(1,int(sw)))
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', svg, re.S):
        a=parse_attrs(m.group(1)); content=re.sub(r'<[^>]+>','',m.group(2)).strip()
        if not content: continue
        x=float(a.get('x',0))*scale; y=float(a.get('y',0))*scale
        sz=float(a.get('font-size',12)); fill=a.get('fill','#000')
        col=hex2rgb(fill) if fill.startswith('#') else (0,0,0)
        fnt=get_font(sz); d.text((x,y), content, font=fnt, fill=col, anchor='mm' if a.get('text-anchor')=='middle' else 'la')
    img.save(out_png,'PNG'); return out_png

P0 = '<font color="#c62828"><b>P0</b></font>'

SVG_25 = '''<svg viewBox="0 0 680 270" xmlns="http://www.w3.org/2000/svg"><defs><marker id="mm1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker></defs>
<rect x="14" y="30" width="140" height="56" rx="8" fill="#0a2540"/><text x="84" y="52" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">调用方</text><text x="84" y="70" text-anchor="middle" font-size="9" fill="#cfe3f7">智能体/工作流/用户</text>
<rect x="200" y="20" width="150" height="76" rx="8" fill="#1565c0"/><text x="275" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">模型网关</text><text x="275" y="60" text-anchor="middle" font-size="9" fill="#e3f0ff">适配器+注册表</text><text x="275" y="76" text-anchor="middle" font-size="9" fill="#e3f0ff">路由/降级</text>
<line x1="154" y1="58" x2="198" y2="58" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/>
<line x1="350" y1="40" x2="408" y2="32" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/>
<line x1="350" y1="72" x2="408" y2="84" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/>
<rect x="410" y="10" width="120" height="44" rx="8" fill="#2e7d32"/><text x="470" y="28" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">公有 API 模型</text><text x="470" y="44" text-anchor="middle" font-size="8.5" fill="#d7f0d7">GPT/Claude/通义</text>
<rect x="410" y="62" width="120" height="44" rx="8" fill="#2e7d32"/><text x="470" y="80" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">私有化模型</text><text x="470" y="96" text-anchor="middle" font-size="8.5" fill="#d7f0d7">自部署开源</text>
<line x1="275" y1="96" x2="89" y2="168" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4" marker-end="url(#mm1)"/>
<text x="150" y="140" text-anchor="middle" font-size="9" fill="#ef6c00">统一计量埋点</text>
<rect x="14" y="170" width="150" height="44" rx="8" fill="#0a2540"/><text x="89" y="188" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">计量埋点</text><text x="89" y="204" text-anchor="middle" font-size="8.5" fill="#cfe3f7">token/cost</text>
<rect x="200" y="170" width="150" height="44" rx="8" fill="#1565c0"/><text x="275" y="188" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">用量统计</text><text x="275" y="204" text-anchor="middle" font-size="8.5" fill="#e3f0ff">租户/部门/智能体</text>
<rect x="410" y="170" width="120" height="44" rx="8" fill="#1565c0"/><text x="470" y="188" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">计费/配额</text><text x="470" y="204" text-anchor="middle" font-size="8.5" fill="#e3f0ff">预算/告警</text>
<rect x="560" y="170" width="110" height="44" rx="8" fill="#ef6c00"/><text x="615" y="188" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">看板/告警</text><text x="615" y="204" text-anchor="middle" font-size="8.5" fill="#ffe6cc">成本报表</text>
<line x1="164" y1="192" x2="198" y2="192" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/>
<line x1="350" y1="192" x2="408" y2="192" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/>
<line x1="530" y1="192" x2="558" y2="192" stroke="#1565c0" stroke-width="2" marker-end="url(#mm1)"/></svg>'''

ADAPT_TAB = [
    ("统一接口 ModelAdapter", "声明 chat/embed/rerank 与能力(流式/上下文/模态/价格)", "接口固定，长期不变"),
    ("具体适配器", "每个模型一个实现(OpenAI/Claude/通义/自部署开源)", "新增模型只加一个适配器类"),
    ("模型注册表 Registry", "登记模型 ID/状态/路由权重/配额", "热插拔：注册/停用不改业务"),
    ("路由策略", "按任务/成本/质量/可用性选模型，支持降级", "策略可配置"),
]
BILL_TAB = [
    ("配额", "免费额度 / 部门月度预算 / 按量计费(内部结算或外部账单)"),
    ("高成本管控", "昂贵/敏感模型需审批或单独配额，避免滥用"),
    ("超阈告警+熔断", "接近预算告警，超限熔断或自动降级更便宜模型"),
    ("内部结算报表", "按成本中心出部门账单，支撑 IT 向业务分摊"),
]
W = A4[0] - 2*16*mm

def build():
    E = []
    E.append(Paragraph("大模型接入与计量计费", styles['title']))
    E.append(Paragraph("企业 AI 智能体中台 · 评审包附册 · 第 25 章 · 通用版 · v1.11 · 2026-10-31", styles['sub']))
    E.append(Paragraph("25. 大模型接入与计量计费（可插拔模型 + Token 统计） " + P0, styles['h2']))
    E.append(Paragraph("平台必须回答两个问题——<b>“用哪个大模型”与“花了多少”</b>。前者通过<b>模型网关+适配器+注册表</b>做成可插拔接口（新增模型=加适配器+一条配置，不改业务代码）；后者通过<b>统一计量埋点→用量统计→计费/配额</b>闭环，按租户/部门/用户/智能体核算成本，且不绕过第 18 章单一过滤点。", styles['body']))
    E.append(Paragraph("25.1 为什么可插拔", styles['h3']))
    for t in ["避免锁定：厂商与模型迭代快，业务代码不应写死某家接口", "最优性价比：简单问答用便宜小模型、复杂推理用大模型，按任务路由降本", "混合部署：公有 API 与私有化开源并存，敏感数据走本地模型"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("25.2 模型网关与适配器（复用第 4 章 One API / LiteLLM）", styles['h3']))
    E.append(make_table(["组成", "职责", "扩展方式"], ADAPT_TAB, [120, 250, 120]))
    E.append(Paragraph("25.3 模型接入流程（5 步）", styles['h3']))
    for i,t in enumerate(["Step1 申请/部署：公有模型申请 Key 或自建额度；私有化完成部署与压测", "Step2 实现/选用适配器：统一 chat/embed/rerank 签名", "Step3 注册到 Registry：登记模型 ID/能力/单价/状态", "Step4 配置路由与配额：设策略与部门配额，高成本模型单独审批", "Step5 灰度上线+监控：小流量灰度，盯用量与质量后转全量"], 1):
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("25.4 Token 用量统计（统一计量的“统计点”）", styles['h3']))
    for t in ["统一埋点：所有调用必经模型网关，按 tenant/dept/user/agent/session 采集 prompt/completion tokens 与 cost", "与第20章演化共用采集层：一次采集同时满足演化评估与用量计费", "成本归因：实时计数+异步落库，归因到部门、智能体、具体任务", "用量看板：部门/个人排名、智能体消耗 Top、本月成本、剩余额度"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("25.5 计费与配额体系（统一管理的“计费点”）", styles['h3']))
    E.append(make_table(["机制", "说明"], BILL_TAB, [120, 370]))
    E.append(Paragraph("25.6 计量计费架构图", styles['h3']))
    png = render_svg(SVG_25, os.path.join(BASE, "_s25.png"), 2)
    im = PILImage.open(png); r = im.size[0]/im.size[1]
    E.append(Image(png, width=470, height=470/r))
    E.append(Paragraph("▲ 图示：调用方→模型网关(适配器+注册表,热插拔)→模型集群；旁路统一计量→用量统计→计费配额→看板告警", styles['small']))
    E.append(Paragraph("25.7 与权限/安全衔接", styles['h3']))
    E.append(Paragraph("用量数据同样受 scope 约束：部门只能看到本部门消耗，成本属敏感指标，跨部门汇总仅平台管理员可见；高成本/敏感模型调用仍需第 19 章 L3 人工审核。计量与计费不绕过第 18 章单一过滤点。", styles['note']))
    return E

def main():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=15*mm, bottomMargin=15*mm, title="大模型接入与计量计费")
    doc.build(build())
    print("SAVED:", OUT)

if __name__ == '__main__':
    main()
