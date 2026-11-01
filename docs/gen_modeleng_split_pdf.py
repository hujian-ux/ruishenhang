# -*- coding: utf-8 -*-
"""把第 35–44 章拆成「拆分下发版」A4 PDF：每章独立成页，可单独打印/下发。
复用 gen_production_split_pdf.py 的卡片样式与 html_to_docx.render_svg。"""
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
F_MSYH = r"C:\Windows\Fonts\msyh.ttc"; F_MSYHBD = r"C:\Windows\Fonts\msyhbd.ttc"
pdfmetrics.registerFont(TTFont("YaHei", F_MSYH, subfontIndex=0))
pdfmetrics.registerFont(TTFont("YaHei-Bold", F_MSYHBD, subfontIndex=0))
registerFontFamily("YaHei", normal="YaHei", bold="YaHei-Bold", italic="YaHei", boldItalic="YaHei-Bold")

NAVY=colors.HexColor("#0a2540"); BLUE=colors.HexColor("#1565c0"); INK=colors.HexColor("#1f2d3d")
MUTED=colors.HexColor("#5b7185"); LINE=colors.HexColor("#d7e0ea"); HEADBG=colors.HexColor("#1565c0")
CODEBG=colors.HexColor("#f4f6f9"); CW=178*mm
P0=colors.HexColor("#dc2626"); P1=colors.HexColor("#d97706"); P2=colors.HexColor("#2563eb")
S={
 "header":ParagraphStyle("header",fontName="YaHei",fontSize=8.5,textColor=MUTED,alignment=TA_CENTER,leading=12),
 "title":ParagraphStyle("title",fontName="YaHei-Bold",fontSize=14,textColor=NAVY,alignment=TA_LEFT,leading=18,spaceBefore=2,spaceAfter=3),
 "sub":ParagraphStyle("sub",fontName="YaHei",fontSize=8.5,textColor=MUTED,alignment=TA_LEFT,leading=12),
 "intro":ParagraphStyle("intro",fontName="YaHei",fontSize=9,textColor=INK,leading=13,alignment=TA_LEFT),
 "body":ParagraphStyle("body",fontName="YaHei",fontSize=8.5,textColor=INK,leading=11.5,alignment=TA_LEFT),
 "bullet":ParagraphStyle("bullet",fontName="YaHei",fontSize=8.5,textColor=INK,leading=12,alignment=TA_LEFT,leftIndent=10),
 "hcell":ParagraphStyle("hcell",fontName="YaHei-Bold",fontSize=8.5,textColor=colors.white,leading=11),
 "lbl":ParagraphStyle("lbl",fontName="YaHei-Bold",fontSize=8.5,textColor=NAVY,leading=11.5),
}
def Hcell(t): return Paragraph(t,S["hcell"])
def build_table(data,cw):
    t=Table(data,colWidths=cw,repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),HEADBG),("VALIGN",(0,0),(-1,-1),"TOP"),
        ("GRID",(0,0),(-1,-1),0.5,LINE),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),("BACKGROUND",(0,1),(-1,-1),colors.white)]))
    return t
def bullets(items): return [Paragraph("• "+x,S["bullet"]) for x in items]
def page(tag,no,title,prio,flow):
    pc={"P0":P0,"P1":P1,"P2":P2}[prio]
    badge=Table([[Paragraph("<font color='white'><b>%s</b></font>"%prio,S["hcell"])]],colWidths=[12*mm])
    badge.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),pc),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    out=[Paragraph(tag+" · %s"%no,S["header"]),
         Table([[badge,Paragraph("%s. %s"%(no,title),S["title"])]],colWidths=[14*mm,164*mm],
                style=TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),0)]))]
    out.extend(flow); out.append(PageBreak()); return out

# SVG 渲染
html_text=open(os.path.join(BASE,"企业AI智能体中台蓝图.html"),encoding="utf-8").read()
blocks=re.findall(r"<svg\b.*?</svg>",html_text,re.S)
def grab(l): return next((b for b in blocks if 'aria-label="%s"'%l in b),None)
svg35=grab("模型选型决策树"); svg36=grab("RAG质量管线")
png35=os.path.join(BASE,"_s35.png"); png36=os.path.join(BASE,"_s36.png")
if svg35: H.render_svg(svg35,png35)
if svg36: H.render_svg(svg36,png36)

TAG="企业 AI 智能体中台 · 模型工程补强 · 拆分下发 · v1.15"
e=[]
e+=page(TAG,"35","模型选型决策框架","P0",[
 Paragraph("缺陷：把 AI 等同 LLM+RAG，用 LLM 做确定性/批量任务→成本高数倍、不可复现、不可解释。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "建选型决策树（输入类型→确定性→可解释→成本），嵌入“新建智能体”向导并强制填写选型说明。",
 "第25章网关新增 adapter_type{llm, ml_serving, rule}，传统ML/规则也走同一网关，统一 caller 注入与计量。",
 "默认策略：确定性/结构化/可批量→非LLM；仅模糊/生成/推理→LLM；关键决策引入LLM须人机共签（第33章）。",
 "建成本/质量对比看板，同一任务并行跑两类模型沉淀选型知识库。",
])+([Spacer(1,2*mm),Image(png35,width=CW,height=CW*250/680),Paragraph("图 35.2 选型决策树",S["sub"])] if os.path.exists(png35) else []))

e+=page(TAG,"36","RAG 质量工程","P0",[
 Paragraph("缺陷：纯向量检索漏 30–50% 相关文档；只做权限过滤≠检索质量。",S["intro"]),Spacer(1,2*mm),
 build_table([
  [Hcell("环节"),Hcell("做法"),Hcell("指标/门禁")],
  [Paragraph("召回",S["body"]),Paragraph("BM25(关键词/专名)+向量，RRF/学习排序融合",S["body"]),Paragraph("Recall@100≥0.95",S["body"])],
  [Paragraph("重排",S["body"]),Paragraph("Top-100 用 cross-encoder 重排至 Top-K",S["body"]),Paragraph("MRR/nDCG 提升",S["body"])],
  [Paragraph("切分",S["body"]),Paragraph("语义切分+父子文档，保留来源/章节/权限元数据",S["body"]),Paragraph("片段可溯源",S["body"])],
  [Paragraph("评测",S["body"]),Paragraph("≥200 条 golden queries + 标准 doc_id 集",S["body"]),Paragraph("CI 门禁阻断",S["body"])],
 ], [20*mm,98*mm,60*mm]),Spacer(1,2*mm)
]+bullets([
 "两层过滤：检索阶段先保“找得全”（质量层），再在第18章单一过滤点做“看不见”（合规层），二者解耦。",
])+([Spacer(1,2*mm),Image(png36,width=CW,height=CW*180/680),Paragraph("图 36.2 混合检索 + 重排 + 质量门禁",S["sub"])] if os.path.exists(png36) else []))

e+=page(TAG,"37","ML 级评测体系","P0",[
 Paragraph("缺陷：评测依赖 LLM-as-judge，人机相关性仅 0.59–0.66 会一致错，演化飞轮地基不可靠。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "客观指标用规则校验：格式/工具参数/检索命中/引用/越权拦截，不靠 LLM。",
 "建 ≥500 条黄金集（多轮+边界）作回归基线，每次改动必跑。",
 "LLM-as-judge 仅辅助：多模型交叉+校准阈值，不单独定生死。",
 "人工抽检+用户反馈回流第20章演化；演化建议须过评测门禁（黄金集不退化）才灰度。",
]))

e+=page(TAG,"38","非 LLM 模型 MLOps","P1",[
 Paragraph("缺陷：机器学习方案只有推理调用，缺训练/特征/版本/漂移/重训。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "特征存储：离线+在线统一管理，训练-serving 一致。",
 "训练流水线：清洗→特征→训练→评估→注册，可定时重跑。",
 "模型注册表与版本：与第25章网关同表（adapter=ml_serving）。",
 "漂移检测：PSI/KS 监控输入分布与效果衰减，超阈告警。",
 "重训与回滚：漂移触发重训，灰度+旧版可回滚。",
]))

e+=page(TAG,"39","幻觉抑制与 Grounding","P1",[
 Paragraph("缺陷：第27章只列威胁无手段，企业最怕说错话。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "引用强制：每段附可点击来源，无来源不得生成事实断言。",
 "事实校验：生成后做声明-证据比对，证据不在检索集则标记/拒答。",
 "置信校准与拒答：低置信/无支撑走拒答或转人工（第42章）。",
 "输出合规校验层：生成后规则/小模型过滤敏感与越权。",
]))

e+=page(TAG,"40","成本与延迟优化","P1",[
 Paragraph("缺陷：第25章有计费无优化；无路由无语义缓存，规模上来成本失控。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "任务路由器：简单→小模型，复杂→大模型，策略可配。",
 "语义缓存：相似 Query 命中缓存（embedding 相似度阈值）。",
 "模型分层与预算：注册表标 tier/单价，高成本单独审批，超预算熔断。",
 "高频请求预计算，命中即返回。",
]))

e+=page(TAG,"41","模型质量可观测性","P1",[
 Paragraph("缺陷：第25章只记 token/成本，不记成功率/幻觉率/转人工率。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "扩展计量埋点：成功率/幻觉率/转人工率/满意度/延迟 p95。",
 "与第26章指标-日志-链路三支柱打通，按 tenant/dept/agent 下钻。",
 "质量告警：幻觉率/转人工率突增自动告警触发演化复盘。",
 "质量看板受 scope 约束，成本/质量仅管理员可见。",
]))

e+=page(TAG,"42","优雅降级与人工升级","P2",[
 Paragraph("缺陷：Agent 静默失败或硬报错，无置信阈值触发升级。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "置信阈值：每答带置信分，低于阈值走降级链（小模型→规则→人工）。",
 "降级链配置：每智能体可配降级顺序与兜底话术。",
 "人工升级：转人工带完整上下文与置信理由，进工单/审批流。",
]))

e+=page(TAG,"43","中文语言特化","P2",[
 Paragraph("缺陷：Embedding/LLM 需中文优化未单列，分词专名易错。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "默认中文 embedding（bge/bce 系列），第25章注册表可切。",
 "检索切分考虑中文语义边界，避免标点粗暴切。",
 "建中文少样本/系统 Prompt 模板库，规避翻译腔。",
 "用 C-Eval/CMMLU/内部中文集做回归基准。",
]))

e+=page(TAG,"44","Agent/工具层安全闭环","P2",[
 Paragraph("缺陷：第18章过滤点只在检索层；工具调用/外部API 可绕过权限外泄。",S["intro"]),Spacer(1,2*mm)
]+bullets([
 "工具调用也过权限：每工具声明 scope/数据域，调用前校验 caller。",
 "外部 API 沙箱与脱敏：去手机/身份证/密钥，仅白名单域名。",
 "工具审计：入参/出参/调用者进统一审计流（第29章）。",
 "复用第18章单一过滤点代码，不另起炉灶。",
]))

doc=SimpleDocTemplate(os.path.join(BASE,"模型工程补强_拆分下发.pdf"),pagesize=A4,
    leftMargin=16*mm,rightMargin=16*mm,topMargin=14*mm,bottomMargin=14*mm,title="模型工程补强 · 拆分下发")
doc.build(e)
print("SAVED:",os.path.join(BASE,"模型工程补强_拆分下发.pdf"))
for f in [png35,png36]:
    if os.path.exists(f): os.remove(f)
print("temp cleaned")
