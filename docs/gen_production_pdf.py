# -*- coding: utf-8 -*-
"""第26-34章（生产化与运营化补强）-> 评审包附册 A4 PDF"""
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
OUT = os.path.join(BASE, "生产化与运营化补强_A4.pdf")
HTML = os.path.join(BASE, "企业AI智能体中台蓝图.html")

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
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', svg, re.S):
        a=parse_attrs(m.group(1)); content=re.sub(r'<[^>]+>','',m.group(2)).strip()
        if not content: continue
        x=float(a.get('x',0))*scale; y=float(a.get('y',0))*scale
        sz=float(a.get('font-size',12)); fill=a.get('fill','#000')
        col=hex2rgb(fill) if fill.startswith('#') else (0,0,0)
        fnt=get_font(sz); d.text((x,y), content, font=fnt, fill=col, anchor='mm' if a.get('text-anchor')=='middle' else 'la')
    img.save(out_png,'PNG'); return out_png

raw = open(HTML, encoding='utf-8').read()
def grab(label):
    m = re.search(r'<svg[^>]*aria-label="%s"[^>]*>(.*?)</svg>' % label, raw, re.S)
    return ('<svg ' + m.group(0).split('<svg',1)[1]) if m else None
svg_obs = grab('s26-obs'); svg_sec = grab('s27-sec'); svg_eval = grab('s28-eval')
img_obs = render_svg(svg_obs, os.path.join(BASE,'_s26.png')) if svg_obs else None
img_sec = render_svg(svg_sec, os.path.join(BASE,'_s27.png')) if svg_sec else None
img_eval = render_svg(svg_eval, os.path.join(BASE,'_s28.png')) if svg_eval else None

W, H = A4
LM = RM = 16*mm
CW = W - LM - RM
P = []
def add(el): P.append(el)
def bullet(txt): add(Paragraph("• " + txt, styles['bullet']))
def h2(t): add(Paragraph(t, styles['h2']))
def h3(t): add(Paragraph(t, styles['h3']))

# ---- 封面 ----
add(Paragraph("生产化与运营化补强 · 评审附册", styles['title']))
add(Paragraph("企业 AI 智能体中台蓝图 v1.13 · 第 26–34 章 · 通用版 · 内部使用", styles['sub']))
add(Paragraph("补齐从“设计态”到“生产态 + 运营态”的缺口：P0 上生产硬门槛、P1 政企验收与推广、P2 规模化与合规兜底。", styles['body']))
add(Spacer(1,4))

# ---- 26 部署运维与可观测性 ----
h2("26. 部署运维与可观测性（P0）")
h3("26.1 部署形态与资源规划")
add(make_table(["规模","部署形态","关键组件","资源建议"],
    [["试点/MVP","单机 Docker Compose","Dify+Qdrant+PG+MinIO+网关","8C16G 起；向量盘≥100G"],
     ["多部门","Docker Swarm / K8s","无状态多副本；PG 主从","16C32G×2+；独立 GPU"],
     ["集团/高并发","K8s + HPA","网关限流、向量集群","按 QPS 弹性；冷备+异地"]],
    [CW*0.16, CW*0.24, CW*0.34, CW*0.26]))
h3("26.2 高可用、容灾与可观测性")
bullet("RTO ≤ 30 分钟、RPO ≤ 5 分钟；网关/应用/向量库均多副本，无单点。")
bullet("三支柱：Metrics(Prometheus+Grafana)、Logs(Loki/ELK)、Traces(OpenTelemetry)，统一关联 tenant/dept/user。")
bullet("灰度与回滚：提示词/知识库/工作流版本化，新模型先金丝雀 5% 再全量；回滚优先于修复。")
bullet("告警分级 P1/P2/P3 对接 IM 值班群：错误率>1%、召回 P95>3s、备份失败等。")
if img_obs: add(Spacer(1,3)); add(Image(img_obs, width=CW, height=CW*150/680)); add(Paragraph("图 26.3 可观测性数据流：采集→存储→看板/告警", styles['small']))

# ---- 27 AI 行为安全 ----
add(PageBreak())
h2("27. AI 行为安全（P0）")
bullet("输入侧：提示注入/越狱检测、敏感指令拦截、输入清洗（截断/防路径遍历）。")
bullet("知识侧（防投毒）：入库来源可信度校验、敏感文档审核、更新留痕可溯源回滚。")
bullet("输出侧：内容安全过滤（违规/歧视/泄密）、幻觉缓解（强制引用溯源+低置信拒答）、越权二次防线（输出层再校验 scope）。")
bullet("人工兜底：高风险操作必人工确认；AI 输出不作最终决策唯一依据。")
if img_sec: add(Spacer(1,3)); add(Image(img_sec, width=CW, height=CW*150/680)); add(Paragraph("图 27 输入—知识—输出三层防护，安全护栏贯穿全链路", styles['small']))

# ---- 28 测试与评测 ----
h2("28. 测试与评测体系（P0）")
add(make_table(["层","对象","方法","门禁"],
    [["单元","工具/函数/适配器","断言输入输出","覆盖率≥70%"],
     ["集成","工作流/多Agent","端到端真实任务","关键路径全通"],
     ["评测","问答/生成质量","golden set + LLM-as-judge","不劣于基线"]],
    [CW*0.14, CW*0.30, CW*0.34, CW*0.22]))
bullet("RAG 评测集：每部门 问题+标准答案+标准引用（≥50 条），指标=召回准确率/答案忠实度/引用完整度。")
bullet("LLM-as-judge 评分卡：相关性/合规性/引用完整/无害性 0–5 分；高风险部门人工抽检。")
bullet("上线验收阈值示例：准确率≥85%、可引用率≥90%、越权拦截率 100%。")
if img_eval: add(Spacer(1,3)); add(Image(img_eval, width=CW, height=CW*140/680)); add(Paragraph("图 28 评测闭环：评测是自动演化（第 20 章）的效果度量尺", styles['small']))

# ---- 29 审计与合规 ----
add(PageBreak())
h2("29. 审计与合规留存（P1）")
add(make_table(["字段","含义","示例"],
    [["who","操作人+角色","张三 / 部门管理员"],
     ["what","动作+对象","查询知识 / 导出文档"],
     ["scope","可见范围上下文","dept=研发部"],
     ["when","时间戳","ISO8601+时区"],
     ["result","成功/拒绝+原因","拒绝：越权"]],
    [CW*0.18, CW*0.40, CW*0.42]))
bullet("留存：操作日志≥180 天、安全事件≥1 年；审计库独立、只读、加密。")
bullet("合规映射：等保 2.0 三级、ISO 27001、数据安全法、个人信息保护。")
bullet("数据主体权利：员工可导出/更正/删除本人数据，删除级联清理向量与索引。")

# ---- 30 运营推广 ----
h2("30. 运营推广与价值度量（P1）")
bullet("推广三阶段：试点（1–2 高意愿部门样板）→ 放大（沉淀模板中心复制）→ 常态化（纳入考核/日报）。")
bullet("培训：管理员（权限/知识库/编排）、种子用户（业务转智能体）、全员（怎么问、看什么范围）。")
bullet("度量：活跃度（DAU/WAU、采纳率、任务完成率、部门渗透率）；价值 ROI（节省工时、提速、质量、结合第 25 章成本算净收益）。")
bullet("反馈闭环接入第 20 章自动演化：点赞/纠错 → 知识缺口 → 自生长 → 评测验证 → 灰度发布。")

# ---- 31 集成 ----
h2("31. 统一身份与集成架构（P1）")
add(make_table(["集成对象","方式","场景"],
    [["企微/钉钉/飞书","机器人+侧边栏+推送","对话入口、通知、审批"],
     ["OA/审批","OpenAPI/Webhook","流程联动、单据生成"],
     ["ERP/CRM","连接器+数据回流","客户/订单查询、报表"]],
    [CW*0.30, CW*0.34, CW*0.36]))
bullet("SSO 深化：OIDC/SAML/LDAP，目录同步；入/转/离自动开通回收，权限随岗位变更。")
bullet("事件总线/Webhook：知识入库、审批通过等事件异步触发工作流；集成调用同样走第 18 章过滤点。")

# ---- 32-34 P2 ----
add(PageBreak())
h2("32. 多租户与集团化隔离（P2）")
bullet("层级：租户(tenant) > 部门(dept) > 个人；权限元数据增 tenant_id。")
bullet("隔离粒度：默认逻辑隔离（同库不同 tenant_id）；高敏客户可选物理隔离（独立库/部署）。")
bullet("集团级：统一策略下发、跨租户白名单协作、按租户配额/计费分摊（衔接第 25 章）。")

h2("33. 法律与责任界定（P2）")
add(make_table(["事项","建议约定"],
    [["AI 生成内容归属","职务作品归企业；员工对最终采用负责"],
     ["责任边界","AI 输出不替代专业判断；关键决策人机共签"],
     ["免责声明","对外内容标注“AI 辅助生成，请核实”"]],
    [CW*0.34, CW*0.66]))
bullet("使用守则：禁止把机密/个人信息输入公网模型；内部平台按 scope 使用。")
bullet("数据合规：遵守个人信息保护法；境外模型需评估数据出境合规。")

h2("34. 性能与成本优化（P2）")
bullet("缓存：召回结果缓存、热点知识/模型响应缓存（Redis）。")
bullet("并发与限流：网关节流、请求队列削峰、模型并发受配额约束。")
bullet("按难度路由：简单任务走小模型、复杂才用大模型，混合推理显著降本。")
bullet("向量库优化：HNSW 索引、分片、冷热分离；效果在成本看板（第 25 章）可观测。")
add(Spacer(1,6))
add(Paragraph("小结：第 1–25 章为“设计态”功能规划；26–28 是上生产硬门槛；29–31 决定能否被政企验收并真正用起来；32–34 为规模化与合规兜底。全平台始终复用第 5/13/18 章权限与单一过滤模型，能力越多、边界越稳。", styles['note']))

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=15*mm, bottomMargin=14*mm,
                        title="生产化与运营化补强_A4", author="睿深行")
doc.build(P)
print("SAVED:", OUT)
for f in ['_s26.png','_s27.png','_s28.png']:
    p=os.path.join(BASE,f)
    if os.path.exists(p): os.remove(p)
print("temp cleaned")
