# -*- coding: utf-8 -*-
"""把第 26–34 章拆成「拆分下发版」A4 PDF：每章独立成页，可单独打印/下发。
对应九部门卡片拆分件（九部门落地卡片_拆分.pdf）与移动端拆分件做法。中文：msyh.ttc。
"""
import os, re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
import html_to_docx as H

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
F_MSYH = r"C:\Windows\Fonts\msyh.ttc"
F_MSYHBD = r"C:\Windows\Fonts\msyhbd.ttc"
pdfmetrics.registerFont(TTFont("YaHei", F_MSYH, subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHei-Bold", F_MSYHBD, subfontIndex=0))
registerFontFamily("YaHei", normal="YaHei", bold="YaHei-Bold", italic="YaHei", boldItalic="YaHei-Bold")

NAVY = colors.HexColor("#0a2540")
BLUE = colors.HexColor("#1565c0")
INK = colors.HexColor("#1f2d3d")
MUTED = colors.HexColor("#5b7185")
LINE = colors.HexColor("#d7e0ea")
HEADBG = colors.HexColor("#1565c0")
CODEBG = colors.HexColor("#f4f6f9")
CW = 178*mm

S = {
    "header": ParagraphStyle("header", fontName="YaHei", fontSize=8.5, textColor=MUTED, alignment=TA_CENTER, leading=12),
    "title": ParagraphStyle("title", fontName="YaHei-Bold", fontSize=14, textColor=NAVY, alignment=TA_LEFT, leading=18, spaceBefore=2, spaceAfter=3),
    "sub": ParagraphStyle("sub", fontName="YaHei", fontSize=8.5, textColor=MUTED, alignment=TA_LEFT, leading=12),
    "intro": ParagraphStyle("intro", fontName="YaHei", fontSize=9, textColor=INK, leading=13, alignment=TA_LEFT),
    "body": ParagraphStyle("body", fontName="YaHei", fontSize=8.5, textColor=INK, leading=11.5, alignment=TA_LEFT),
    "bullet": ParagraphStyle("bullet", fontName="YaHei", fontSize=8.5, textColor=INK, leading=12, alignment=TA_LEFT, leftIndent=10),
    "hcell": ParagraphStyle("hcell", fontName="YaHei-Bold", fontSize=8.5, textColor=colors.white, leading=11),
    "code": ParagraphStyle("code", fontName="YaHei", fontSize=7.3, textColor=colors.HexColor("#1f2d3d"), leading=9.6, alignment=TA_LEFT),
    "lbl": ParagraphStyle("lbl", fontName="YaHei-Bold", fontSize=8.5, textColor=NAVY, leading=11.5),
    "note": ParagraphStyle("note", fontName="YaHei", fontSize=8, textColor=MUTED, leading=11),
}

def Hcell(t): return Paragraph(t, S["hcell"])
def build_table(data, colWidths):
    t = Table(data, colWidths=colWidths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), HEADBG),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.5, LINE),
        ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("BACKGROUND", (0,1), (-1,-1), colors.white),
    ]))
    return t
def code_block(text):
    inner = Paragraph(text.replace(" ", "&nbsp;").replace("\n", "<br/>"), S["code"])
    t = Table([[inner]], colWidths=[CW])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),CODEBG),("BOX",(0,0),(-1,-1),0.5,LINE),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    return t
def bullets(items):
    return [Paragraph("• " + x, S["bullet"]) for x in items]

def page(header_text, title, content_flowables):
    out = [Paragraph(header_text, S["header"]), Paragraph(title, S["title"])]
    out.extend(content_flowables)
    out.append(PageBreak())
    return out

# ---- 渲染第26-28章的3张SVG ----
html_text = open(os.path.join(BASE, "企业AI智能体中台蓝图.html"), encoding="utf-8").read()
blocks = re.findall(r"<svg\b.*?</svg>", html_text, re.S)
def grab_svg(label):
    return next((b for b in blocks if 'aria-label="%s"' % label in b), None)
svg_obs = grab_svg("s26-obs"); svg_sec = grab_svg("s27-sec"); svg_eval = grab_svg("s28-eval")
png_obs = os.path.join(BASE, "_s26.png"); png_sec = os.path.join(BASE, "_s27.png"); png_eval = os.path.join(BASE, "_s28.png")
if svg_obs: H.render_svg(svg_obs, png_obs)
if svg_sec: H.render_svg(svg_sec, png_sec)
if svg_eval: H.render_svg(svg_eval, png_eval)

e = []
TAG = "企业 AI 智能体中台 · 生产化补强 · 拆分下发 · v1.14"

# 26
e += page(TAG + " · 26", "26. 部署运维与可观测性（P0）", [
    Paragraph("第 15 章是部署检查清单；本章补齐“上线后”工程底座：挂了谁知道、慢了查哪里、数据丢了怎么恢复。", S["intro"]),
    Spacer(1,2*mm),
    build_table([
        [Hcell("规模"),Hcell("部署形态"),Hcell("关键组件"),Hcell("资源建议")],
        [Paragraph("试点/MVP",S["body"]),Paragraph("单机 Docker Compose",S["body"]),Paragraph("Dify+Qdrant+PG+MinIO+网关",S["body"]),Paragraph("8C16G 起；向量盘≥100G",S["body"])],
        [Paragraph("多部门",S["body"]),Paragraph("Docker Swarm / K8s",S["body"]),Paragraph("无状态多副本；PG 主从",S["body"]),Paragraph("16C32G×2+；独立 GPU",S["body"])],
        [Paragraph("集团/高并发",S["body"]),Paragraph("K8s + HPA",S["body"]),Paragraph("网关限流、向量集群",S["body"]),Paragraph("按 QPS 弹性；异地",S["body"])],
    ], [24*mm,34*mm,62*mm,58*mm]),
    Spacer(1,2*mm),
] + bullets([
    "RTO ≤ 30 分钟、RPO ≤ 5 分钟；网关/应用/向量库均多副本，无单点。",
    "可观测性三支柱：Metrics(Prometheus+Grafana)、Logs(Loki/ELK)、Traces(OpenTelemetry)，统一关联 tenant/dept/user。",
    "灰度与回滚：提示词/知识库/工作流版本化，新模型先金丝雀 5% 再全量；回滚优先于修复。",
    "告警分级 P1/P2/P3 对接 IM 值班群：错误率>1%、召回 P95>3s、备份失败等。",
]) + ([Spacer(1,2*mm), Image(png_obs, width=CW, height=CW*150/680),
       Paragraph("图 26.3 可观测性数据流：采集→存储→看板/告警", S["sub"])] if os.path.exists(png_obs) else []))

# 27
fl27 = [Paragraph("第 6 章覆盖“数据保密”，但企业更常遇到 AI 生成内容本身的风险。本章建立输入—知识—输出三层防护，与第 18 章权限过滤互补。", S["intro"]), Spacer(1,2*mm)]
fl27 += bullets([
    "输入侧：提示注入/越狱检测、敏感指令拦截、输入清洗（截断/防路径遍历）。",
    "知识侧（防投毒）：入库来源可信度校验、敏感文档审核、更新留痕可溯源回滚。",
    "输出侧：内容安全过滤（违规/歧视/泄密）、幻觉缓解（强制引用+低置信拒答）、越权二次防线（输出层再校验 scope）。",
    "人工兜底：高风险操作必人工确认；AI 输出不作最终决策唯一依据。",
])
if os.path.exists(png_sec):
    fl27 += [Spacer(1,2*mm), Image(png_sec, width=CW, height=CW*150/680),
             Paragraph("图 27 输入—知识—输出三层防护，安全护栏贯穿全链路", S["sub"])]
e += page(TAG + " · 27", "27. AI 行为安全（P0）", fl27)

# 28
fl28 = [build_table([
    [Hcell("层"),Hcell("对象"),Hcell("方法"),Hcell("门禁")],
    [Paragraph("单元",S["body"]),Paragraph("工具/函数/适配器",S["body"]),Paragraph("断言输入输出",S["body"]),Paragraph("覆盖率≥70%",S["body"])],
    [Paragraph("集成",S["body"]),Paragraph("工作流/多Agent",S["body"]),Paragraph("端到端真实任务",S["body"]),Paragraph("关键路径全通",S["body"])],
    [Paragraph("评测",S["body"]),Paragraph("问答/生成质量",S["body"]),Paragraph("golden set + LLM-as-judge",S["body"]),Paragraph("不劣于基线",S["body"])],
], [18*mm,38*mm,62*mm,60*mm]), Spacer(1,2*mm)]
fl28 += bullets([
    "RAG 评测集：每部门 问题+标准答案+标准引用（≥50 条），指标=召回准确率/答案忠实度/引用完整度。",
    "LLM-as-judge 评分卡：相关性/合规性/引用完整/无害性 0–5 分；高风险部门人工抽检。",
    "上线验收阈值示例：准确率≥85%、可引用率≥90%、越权拦截率 100%。",
])
if os.path.exists(png_eval):
    fl28 += [Spacer(1,2*mm), Image(png_eval, width=CW, height=CW*140/680),
             Paragraph("图 28 评测闭环：评测是自动演化（第 20 章）的效果度量尺", S["sub"])]
e += page(TAG + " · 28", "28. 测试与评测体系（P0）", fl28)

# 29
e += page(TAG + " · 29", "29. 审计与合规留存（P1）", [
    build_table([
        [Hcell("字段"),Hcell("含义"),Hcell("示例")],
        [Paragraph("who",S["body"]),Paragraph("操作人+角色",S["body"]),Paragraph("张三 / 部门管理员",S["body"])],
        [Paragraph("what",S["body"]),Paragraph("动作+对象",S["body"]),Paragraph("查询知识 / 导出文档",S["body"])],
        [Paragraph("scope",S["body"]),Paragraph("可见范围上下文",S["body"]),Paragraph("dept=研发部",S["body"])],
        [Paragraph("when",S["body"]),Paragraph("时间戳",S["body"]),Paragraph("ISO8601+时区",S["body"])],
        [Paragraph("result",S["body"]),Paragraph("成功/拒绝+原因",S["body"]),Paragraph("拒绝：越权",S["body"])],
    ], [26*mm,66*mm,86*mm]),
    Spacer(1,2*mm),
] + bullets([
    "留存：操作日志≥180 天、安全事件≥1 年；审计库独立、只读、加密。",
    "合规映射：等保 2.0 三级、ISO 27001、数据安全法、个人信息保护。",
    "数据主体权利：员工可导出/更正/删除本人数据，删除级联清理向量与索引。",
]))

# 30
e += page(TAG + " · 30", "30. 运营推广与价值度量（P1）", [
    Paragraph("工具再好没人用等于失败。本章把“建好”推进到“用好”，并让管理层看到价值。", S["intro"]),
    Spacer(1,2*mm),
] + bullets([
    "推广三阶段：试点（1–2 高意愿部门样板）→ 放大（沉淀模板中心复制）→ 常态化（纳入考核/日报）。",
    "培训：管理员（权限/知识库/编排）、种子用户（业务转智能体）、全员（怎么问、看什么范围）。",
    "活跃度：DAU/WAU、采纳率、任务完成率、部门渗透率看板。",
    "价值 ROI：节省工时、响应提速、质量提升，结合第 25 章成本算净收益。",
    "反馈闭环接入第 20 章自动演化：点赞/纠错 → 知识缺口 → 自生长 → 评测验证 → 灰度发布。",
]))

# 31
e += page(TAG + " · 31", "31. 统一身份与集成架构（P1）", [
    build_table([
        [Hcell("集成对象"),Hcell("方式"),Hcell("场景")],
        [Paragraph("企微/钉钉/飞书",S["body"]),Paragraph("机器人+侧边栏+推送",S["body"]),Paragraph("对话入口、通知、审批",S["body"])],
        [Paragraph("OA/审批",S["body"]),Paragraph("OpenAPI/Webhook",S["body"]),Paragraph("流程联动、单据生成",S["body"])],
        [Paragraph("ERP/CRM",S["body"]),Paragraph("连接器+数据回流",S["body"]),Paragraph("客户/订单查询、报表",S["body"])],
    ], [42*mm,52*mm,84*mm]),
    Spacer(1,2*mm),
] + bullets([
    "SSO 深化：OIDC/SAML/LDAP，目录同步；入/转/离自动开通回收，权限随岗位变更。",
    "事件总线/Webhook：知识入库、审批通过等事件异步触发工作流；集成调用同样走第 18 章过滤点。",
]))

# 32
e += page(TAG + " · 32", "32. 多租户与集团化隔离（P2）", [
    Paragraph("当前蓝图以“单公司 + 部门”为主。若客户是集团、多子公司或多品牌，需在部门之上增加“租户（tenant）”一层隔离。", S["intro"]),
    Spacer(1,2*mm),
] + bullets([
    "层级：租户(tenant) > 部门(dept) > 个人；权限元数据增 tenant_id。",
    "隔离粒度：默认逻辑隔离（同库不同 tenant_id）；高敏客户可选物理隔离（独立库/部署）。",
    "集团级：统一策略下发、跨租户白名单协作、按租户配额/计费分摊（衔接第 25 章）。",
]))

# 33
e += page(TAG + " · 33", "33. 法律与责任界定（P2）", [
    build_table([
        [Hcell("事项"),Hcell("建议约定")],
        [Paragraph("AI 生成内容归属",S["body"]),Paragraph("职务作品归企业；员工对最终采用负责",S["body"])],
        [Paragraph("责任边界",S["body"]),Paragraph("AI 输出不替代专业判断；关键决策人机共签",S["body"])],
        [Paragraph("免责声明",S["body"]),Paragraph("对外内容标注“AI 辅助生成，请核实”",S["body"])],
    ], [54*mm,124*mm]),
    Spacer(1,2*mm),
] + bullets([
    "使用守则：禁止把机密/个人信息输入公网模型；内部平台按 scope 使用。",
    "数据合规：遵守个人信息保护法；境外模型需评估数据出境合规。",
]))

# 34
e += page(TAG + " · 34", "34. 性能与成本优化（P2）", [
    Paragraph("规模上来后，延迟与成本会同时成为瓶颈。本章给出可落地优化手段，并与第 25 章计量计费联动。", S["intro"]),
    Spacer(1,2*mm),
] + bullets([
    "缓存：召回结果缓存、热点知识/模型响应缓存（Redis）。",
    "并发与限流：网关节流、请求队列削峰、模型并发受配额约束。",
    "按难度路由：简单任务走小模型、复杂才用大模型，混合推理显著降本。",
    "向量库优化：HNSW 索引、分片、冷热分离；效果在成本看板（第 25 章）可观测。",
]))

doc = SimpleDocTemplate(os.path.join(BASE, "生产化补强_拆分下发.pdf"), pagesize=A4,
                        leftMargin=16*mm, rightMargin=16*mm, topMargin=14*mm, bottomMargin=14*mm,
                        title="生产化补强 · 拆分下发")
doc.build(e)
print("SAVED:", os.path.join(BASE, "生产化补强_拆分下发.pdf"))
for f in [png_obs, png_sec, png_eval]:
    if os.path.exists(f): os.remove(f)
print("temp cleaned")
