# -*- coding: utf-8 -*-
"""第21-24章（自助建部门/工具对接/本地文件/UI规范）-> 评审包附册 A4 PDF"""
import os, re
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
OUT = os.path.join(BASE, "平台扩展与UI规范_A4.pdf")

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

# ---------- SVG renderer (rect/line/text only) ----------
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

import math

SVG_21 = '''<svg viewBox="0 0 680 250" xmlns="http://www.w3.org/2000/svg"><defs><marker id="w1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker></defs>
<rect x="14" y="18" width="118" height="50" rx="8" fill="#0a2540"/><text x="73" y="38" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">发起建部门</text><text x="73" y="56" text-anchor="middle" font-size="9" fill="#cfe3f7">员工/主管</text>
<rect x="158" y="18" width="118" height="50" rx="8" fill="#1565c0"/><text x="217" y="38" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">填信息+选模板</text><text x="217" y="56" text-anchor="middle" font-size="9" fill="#e3f0ff">Step1-2</text>
<rect x="302" y="18" width="118" height="50" rx="8" fill="#1565c0"/><text x="361" y="38" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">自动初始化</text><text x="361" y="56" text-anchor="middle" font-size="9" fill="#e3f0ff">scope/RBAC</text>
<rect x="446" y="18" width="118" height="50" rx="8" fill="#1565c0"/><text x="505" y="38" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">绑可见范围</text><text x="505" y="56" text-anchor="middle" font-size="9" fill="#e3f0ff">Step4</text>
<rect x="590" y="18" width="78" height="50" rx="8" fill="#2e7d32"/><text x="629" y="38" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">审核生效</text><text x="629" y="56" text-anchor="middle" font-size="9" fill="#d7f0d7">Step5</text>
<line x1="132" y1="43" x2="156" y2="43" stroke="#1565c0" stroke-width="2" marker-end="url(#w1)"/>
<line x1="276" y1="43" x2="300" y2="43" stroke="#1565c0" stroke-width="2" marker-end="url(#w1)"/>
<line x1="420" y1="43" x2="444" y2="43" stroke="#1565c0" stroke-width="2" marker-end="url(#w1)"/>
<line x1="564" y1="43" x2="588" y2="43" stroke="#1565c0" stroke-width="2" marker-end="url(#w1)"/>
<line x1="361" y1="68" x2="361" y2="120" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4"/>
<rect x="258" y="120" width="206" height="56" rx="8" fill="#0a2540"/><text x="361" y="142" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">业务梳理引导(对话+画布)</text><text x="361" y="160" text-anchor="middle" font-size="9" fill="#cfe3f7">引导提问→步骤→节点→草图</text>
<line x1="361" y1="176" x2="361" y2="210" stroke="#1565c0" stroke-width="2" marker-end="url(#w1)"/>
<rect x="230" y="210" width="262" height="32" rx="8" fill="#2196f3"/><text x="361" y="231" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">一键推送到 L2 编排器→发布</text></svg>'''

SVG_22 = '''<svg viewBox="0 0 680 230" xmlns="http://www.w3.org/2000/svg"><defs><marker id="t1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker></defs>
<rect x="14" y="20" width="150" height="56" rx="8" fill="#0a2540"/><text x="89" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">智能体/工作流</text><text x="89" y="60" text-anchor="middle" font-size="9" fill="#cfe3f7">自然语言任务</text>
<rect x="200" y="20" width="150" height="56" rx="8" fill="#1565c0"/><text x="275" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">工具注册中心</text><text x="275" y="60" text-anchor="middle" font-size="9" fill="#e3f0ff">统一纳管+鉴权</text>
<line x1="164" y1="48" x2="198" y2="48" stroke="#1565c0" stroke-width="2" marker-end="url(#t1)"/>
<line x1="350" y1="40" x2="396" y2="30" stroke="#1565c0" stroke-width="2" marker-end="url(#t1)"/>
<line x1="350" y1="48" x2="396" y2="48" stroke="#1565c0" stroke-width="2" marker-end="url(#t1)"/>
<line x1="350" y1="56" x2="396" y2="66" stroke="#1565c0" stroke-width="2" marker-end="url(#t1)"/>
<rect x="400" y="8" width="120" height="44" rx="8" fill="#2196f3"/><text x="460" y="26" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">API 网关</text><text x="460" y="42" text-anchor="middle" font-size="8.5" fill="#eaf4ff">CRM/ERP</text>
<rect x="400" y="38" width="120" height="44" rx="8" fill="#2196f3"/><text x="460" y="56" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">CLI 沙箱</text><text x="460" y="72" text-anchor="middle" font-size="8.5" fill="#eaf4ff">PS/ffmpeg</text>
<rect x="400" y="68" width="120" height="44" rx="8" fill="#2196f3"/><text x="460" y="86" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">桌面桥</text><text x="460" y="102" text-anchor="middle" font-size="8.5" fill="#eaf4ff">CAD/PS插件</text>
<line x1="520" y1="30" x2="556" y2="30" stroke="#2e7d32" stroke-width="2" marker-end="url(#t1)"/>
<line x1="520" y1="60" x2="556" y2="60" stroke="#2e7d32" stroke-width="2" marker-end="url(#t1)"/>
<line x1="520" y1="90" x2="556" y2="90" stroke="#2e7d32" stroke-width="2" marker-end="url(#t1)"/>
<rect x="560" y="8" width="110" height="120" rx="8" fill="#2e7d32"/><text x="615" y="30" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">外部专业工具</text><text x="615" y="52" text-anchor="middle" font-size="9" fill="#d7f0d7">ERP/CRM</text><text x="615" y="70" text-anchor="middle" font-size="9" fill="#d7f0d7">Photoshop</text><text x="615" y="88" text-anchor="middle" font-size="9" fill="#d7f0d7">AutoCAD</text><text x="615" y="106" text-anchor="middle" font-size="9" fill="#d7f0d7">企业网盘</text></svg>'''

SVG_23 = '''<svg viewBox="0 0 680 230" xmlns="http://www.w3.org/2000/svg"><defs><marker id="f1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker></defs>
<rect x="14" y="20" width="140" height="56" rx="8" fill="#0a2540"/><text x="84" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">员工电脑</text><text x="84" y="60" text-anchor="middle" font-size="9" fill="#cfe3f7">桌面客户端</text>
<rect x="180" y="20" width="150" height="56" rx="8" fill="#1565c0"/><text x="255" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">本地文件网关</text><text x="255" y="60" text-anchor="middle" font-size="9" fill="#e3f0ff">授权目录+沙箱</text>
<line x1="154" y1="48" x2="178" y2="48" stroke="#1565c0" stroke-width="2" marker-end="url(#f1)"/>
<line x1="330" y1="48" x2="376" y2="48" stroke="#1565c0" stroke-width="2" marker-end="url(#f1)"/>
<rect x="380" y="20" width="150" height="56" rx="8" fill="#1565c0"/><text x="455" y="42" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">智能体/知识库</text><text x="455" y="60" text-anchor="middle" font-size="9" fill="#e3f0ff">切片/向量化</text>
<line x1="530" y1="48" x2="556" y2="48" stroke="#2e7d32" stroke-width="2" marker-end="url(#f1)"/>
<rect x="560" y="20" width="110" height="56" rx="8" fill="#2e7d32"/><text x="615" y="42" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">存储连接器</text><text x="615" y="60" text-anchor="middle" font-size="9" fill="#d7f0d7">NAS/对象</text>
<line x1="255" y1="76" x2="255" y2="120" stroke="#ef6c00" stroke-width="2" stroke-dasharray="5 4"/>
<rect x="150" y="120" width="210" height="40" rx="8" fill="#0a2540"/><text x="255" y="138" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">权限映射 ACL→scope/owner/dept</text><text x="255" y="153" text-anchor="middle" font-size="9" fill="#cfe3f7">复用第5/18章</text>
<rect x="400" y="120" width="190" height="40" rx="8" fill="#0a2540"/><text x="495" y="138" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">敏感读写/外发→人工审核</text><text x="495" y="153" text-anchor="middle" font-size="9" fill="#cfe3f7">审批节点</text></svg>'''

SVG_24 = '''<svg viewBox="0 0 680 250" xmlns="http://www.w3.org/2000/svg"><defs><marker id="l1" markerWidth="9" markerHeight="9" refX="5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L6,3 L0,6 Z" fill="#1565c0"/></marker></defs>
<rect x="14" y="20" width="360" height="180" rx="10" fill="#0a2540"/><text x="194" y="40" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">PC 桌面端(参考 WorkBuddy)</text>
<rect x="26" y="52" width="40" height="140" rx="6" fill="#11315a"/><text x="46" y="120" text-anchor="middle" font-size="8" fill="#9fc0e8">图标栏</text>
<rect x="72" y="52" width="120" height="140" rx="6" fill="#0f2a4d"/><text x="132" y="120" text-anchor="middle" font-size="8" fill="#9fc0e8">会话/资源</text>
<rect x="198" y="52" width="164" height="140" rx="6" fill="#f6f8fb"/><text x="280" y="70" text-anchor="middle" font-size="8" fill="#1f2d3d">主区</text>
<rect x="208" y="168" width="144" height="16" rx="4" fill="#dbe6f2"/><text x="280" y="180" text-anchor="middle" font-size="7.5" fill="#5b7185">底部输入</text>
<rect x="410" y="30" width="120" height="200" rx="16" fill="#0a2540"/><text x="470" y="50" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">手机端</text>
<rect x="424" y="62" width="92" height="150" rx="6" fill="#f6f8fb"/><text x="470" y="135" text-anchor="middle" font-size="8" fill="#1f2d3d">内容区</text>
<rect x="424" y="214" width="92" height="12" rx="3" fill="#11315a"/><text x="470" y="222" text-anchor="middle" font-size="7.5" fill="#9fc0e8">底部Tab</text>
<line x1="374" y1="110" x2="406" y2="110" stroke="#1565c0" stroke-width="2" stroke-dasharray="4 3" marker-end="url(#l1)"/>
<text x="390" y="104" text-anchor="middle" font-size="8" fill="#1565c0">共用令牌</text></svg>'''

P0 = '<font color="#c62828"><b>P0</b></font>'; P1 = '<font color="#ef6c00"><b>P1</b></font>'

INIT_TAB = [
    ("新建部门", "生成 scope=department:{id}，本部门知识默认隔离", "第5章三级知识库"),
    ("建部门管理员", "默认 RBAC 三角色 + 可见范围继承上级", "第13章权限矩阵"),
    ("克隆模板", "模板内智能体/知识继承新部门 scope，不串部门", "第18章单一过滤点"),
]
TOOL_TAB = [
    ("API 对接", "有接口的 SaaS/系统（CRM/ERP/工单）", "注册 OpenAPI，网关鉴权后调用"),
    ("CLI 命令行", "本机专业软件（PS 批处理、ffmpeg）", "沙箱内拼命令、取产物"),
    ("桌面桥", "需进程级交互（CAD 插件、PS 脚本宿主）", "桌面客户端做本地进程桥"),
]
TOKEN_TAB = [
    ("--wb-navy 主色/侧栏", "#0a2540", "左栏、顶栏、主按钮底"),
    ("--wb-blue 强调", "#1565c0", "链接、激活态、主操作"),
    ("--wb-accent 渐变", "#2d7ff9→#1565c0", "品牌渐变、重要 CTA"),
    ("--wb-bg 主区底", "#f6f8fb", "内容区背景"),
    ("--wb-card 卡片", "#ffffff", "卡片/面板"),
    ("圆角/间距", "12px / 8–16px", "统一组件节奏"),
]

W = A4[0] - 2*16*mm

def build():
    E = []
    E.append(Paragraph("平台扩展能力 + UI 规范", styles['title']))
    E.append(Paragraph("企业 AI 智能体中台 · 评审包附册 · 第 21–24 章 · 通用版 · v1.10 · 2026-10-31", styles['sub']))

    # Ch21
    E.append(Paragraph("21. 自助部门创建与业务梳理引导 " + P0, styles['h2']))
    E.append(Paragraph("很多公司存在平台管理员“事先不知道”的特殊部门（合规审计、研究院、区域合资、临时项目组）。平台必须支持<b>部门自助创建</b>与<b>对话式业务梳理引导</b>，且创建时自动套用第 5/13/18 章权限模型（scope 隔离、RBAC、可见范围继承）。", styles['body']))
    E.append(Paragraph("21.1 部门自助创建向导（5 步）", styles['h3']))
    for t in ["Step1 填部门信息（名称/上级/业务域/描述）", "Step2 选模板（克隆相近模板或空白）",
              "Step3 系统自动初始化 scope 与 RBAC", "Step4 绑可见范围（继承或自定义，受限共享需审批）",
              "Step5 提交→部门/平台管理员审核→生效"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("21.2 业务梳理引导（对话+画布）", styles['h3']))
    E.append(Paragraph("把“业务梳理教练 Agent”升级为引导式画布：引导提问（目标/参与者/输入输出/系统文件/合规红线）→ 自动产出步骤与节点类型 → 生成智能体草图 → 一键推 L2 编排器发布。", styles['body']))
    E.append(Paragraph("21.3 权限自动初始化（与既有模型联动）", styles['h3']))
    E.append(make_table(["创建动作", "自动套用", "依据"], INIT_TAB, [110, 250, 130]))
    E.append(Paragraph("21.4 自助建部门 + 业务梳理引导总流程图", styles['h3']))
    png = render_svg(SVG_21, os.path.join(BASE, "_s21.png"), 2)
    im = PILImage.open(png); r = im.size[0]/im.size[1]
    E.append(Image(png, width=470, height=470/r))
    E.append(Paragraph("▲ 图示：部门自助创建与业务梳理引导闭环", styles['small']))

    E.append(PageBreak())
    # Ch22
    E.append(Paragraph("22. 外部专业工具对接（PS / CAD 等） " + P0, styles['h2']))
    E.append(Paragraph("通过<b>工具注册中心</b>统一纳管三类对接（API/CLI/桌面桥），把 PS、CAD、ERP、网盘变成可调用工具，且所有调用不绕过第 18 章单一过滤点、走沙箱与审计。", styles['body']))
    E.append(Paragraph("22.1 三类对接方式", styles['h3']))
    E.append(make_table(["方式", "适用", "机制"], TOOL_TAB, [96, 210, 184]))
    E.append(Paragraph("22.2 典型场景", styles['h3']))
    for t in ["Photoshop：批处理图片/导出多规格、读 PSD 图层、按 VI 手册合规校验",
              "AutoCAD：解析 DWG/DXF 抽取图元、参数问答、两版图纸 diff 变更清单",
              "ERP/CRM：API 网关查询订单/库存；企业网盘：对象存储知识库联动"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("22.3 工具对接架构图", styles['h3']))
    png = render_svg(SVG_22, os.path.join(BASE, "_s22.png"), 2)
    im = PILImage.open(png); r = im.size[0]/im.size[1]
    E.append(Image(png, width=470, height=470/r))
    E.append(Paragraph("▲ 图示：智能体 → 工具注册中心 → API网关/CLI沙箱/桌面桥 → 外部工具", styles['small']))
    E.append(Paragraph("22.4 安全与隔离", styles['h3']))
    E.append(Paragraph("本地执行（CLI/桌面桥）一律在沙箱内进行，仅能访问被授权目录；远程 API 走网关鉴权；读/写/外发敏感资产触发人工审核；工具产出文件自动继承调用者 scope/owner/dept。", styles['note']))

    E.append(PageBreak())
    # Ch23
    E.append(Paragraph("23. 本地文件与存储调用 " + P0, styles['h2']))
    E.append(Paragraph("通过<b>本地文件网关</b>（轻量桌面客户端）与<b>存储连接器</b>（NAS/SMB/对象存储/企业网盘），让员工“拖进来即用、问即取”，文件权限映射回第 5/18 章的 scope/owner/dept。", styles['body']))
    E.append(Paragraph("23.1 要点", styles['h3']))
    for t in ["授权目录：用户勾选可授权目录，未授权不触达", "受控读写：智能体经授权范围读写，越界拒绝；写/删/外发敏感走审批",
              "存储连接器：SMB/NFS/NAS/对象存储/OneDrive/SharePoint", "拖入即切片向量化，引用保留原路径与片段，ACL 映射到 scope/owner/dept",
              "移动端受限：仅预览/申请授权/读已授权目录，写与外发走审批，默认不推敏感文件"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("23.2 本地文件网关架构图", styles['h3']))
    png = render_svg(SVG_23, os.path.join(BASE, "_s23.png"), 2)
    im = PILImage.open(png); r = im.size[0]/im.size[1]
    E.append(Image(png, width=470, height=470/r))
    E.append(Paragraph("▲ 图示：员工电脑 → 本地文件网关(沙箱) → 智能体/知识库，存储连接器并行，权限映射+人工审核", styles['small']))

    E.append(PageBreak())
    # Ch24
    E.append(Paragraph("24. 平台 UI 设计规范（参考 WorkBuddy，PC + 移动） " + P1, styles['h2']))
    E.append(Paragraph("整套平台（PC 桌面端 + 手机端）视觉、组件、交互<b>统一参考 WorkBuddy</b>：深色侧栏+浅色主区的清晰克制风格、信息密度优先、去营销味。两端共用同一套设计令牌与组件库，仅布局适配。高保真原型见 <b>平台UI原型_参考WorkBuddy.html</b>。", styles['body']))
    E.append(Paragraph("24.1 设计令牌（参考 WorkBuddy）", styles['h3']))
    E.append(make_table(["令牌", "取值", "用途"], TOKEN_TAB, [150, 150, 190]))
    E.append(Paragraph("24.2 布局规范", styles['h3']))
    for t in ["PC：左图标栏(64px) + 资源/会话面板(260px) + 主区；顶部模型选择+新建；底部输入",
              "手机：底部 Tab（对话/资源/工具/我的）+ 顶栏；输入固定底部；向导用全屏步骤条"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    E.append(Paragraph("24.3 PC 与移动端布局对照图", styles['h3']))
    png = render_svg(SVG_24, os.path.join(BASE, "_s24.png"), 2)
    im = PILImage.open(png); r = im.size[0]/im.size[1]
    E.append(Image(png, width=470, height=470/r))
    E.append(Paragraph("▲ 图示：PC 三栏与手机底部 Tab，共用设计令牌（颜色/圆角/字体）", styles['small']))
    E.append(Paragraph("24.4 一致性检查清单", styles['h3']))
    for t in ["PC 与移动共用同一套设计令牌，仅布局适配", "同一组件（会话气泡/输入框/权限标签/步骤条）两端同组件库",
              "权限表现两端一致：全域/部门/个人同色同义", "深色侧栏在移动端收为顶部/底部，主色与激活态不变"]:
        E.append(Paragraph("• " + t, styles['bullet']))
    return E

def main():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=15*mm, bottomMargin=15*mm, title="平台扩展与UI规范")
    doc.build(build())
    print("SAVED:", OUT)

if __name__ == '__main__':
    main()
