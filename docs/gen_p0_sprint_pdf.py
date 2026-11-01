# -*- coding: utf-8 -*-
"""P0 三章（35/36/37）第一迭代 Sprint 任务卡 A4 PDF。RAG(36) 最细化，按周拆解。"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

BASE=r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"
F_MSYH=r"C:\Windows\Fonts\msyh.ttc"; F_MSYHBD=r"C:\Windows\Fonts\msyhbd.ttc"
pdfmetrics.registerFont(TTFont("Y",F_MSYH,subfontIndex=0))
pdfmetrics.registerFont(TTFont("Y-B",F_MSYHBD,subfontIndex=0))
registerFontFamily("Y",normal="Y",bold="Y-B",italic="Y",boldItalic="Y-B")

NAVY=colors.HexColor("#0a2540"); BLUE=colors.HexColor("#1565c0"); INK=colors.HexColor("#1f2d3d")
MUTED=colors.HexColor("#5b7185"); LINE=colors.HexColor("#d7e0ea"); HEAD=colors.HexColor("#1565c0")
P0=colors.HexColor("#dc2626"); SOFT=colors.HexColor("#f4f6f9"); CW=178*mm
S={
 "h":ParagraphStyle("h",fontName="Y",fontSize=8.5,textColor=MUTED,alignment=TA_LEFT,leading=12),
 "t":ParagraphStyle("t",fontName="Y-B",fontSize=15,textColor=NAVY,leading=19,spaceAfter=3),
 "s":ParagraphStyle("s",fontName="Y",fontSize=9,textColor=MUTED,leading=12,spaceAfter=2),
 "goal":ParagraphStyle("goal",fontName="Y-B",fontSize=9.5,textColor=NAVY,leading=13,spaceBefore=2,spaceAfter=3),
 "body":ParagraphStyle("body",fontName="Y",fontSize=8.5,textColor=INK,leading=11.5),
 "hc":ParagraphStyle("hc",fontName="Y-B",fontSize=8.3,textColor=colors.white,leading=10.5),
 "k":ParagraphStyle("k",fontName="Y-B",fontSize=8.5,textColor=NAVY,leading=11.5,spaceBefore=3),
 "b":ParagraphStyle("b",fontName="Y",fontSize=8.5,textColor=INK,leading=12,leftIndent=10),
}
def Hc(t): return Paragraph(t,S["hc"])
def tbl(data,cw):
    t=Table(data,colWidths=cw,repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),HEAD),("VALIGN",(0,0),(-1,-1),"TOP"),
        ("GRID",(0,0),(-1,-1),0.5,LINE),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,SOFT])]))
    return t
def bl(items): return [Paragraph("• "+x,S["b"]) for x in items]

def sprint_card(no,title,goal,rows,cw,note=None):
    badge=Table([[Paragraph("<font color='white'><b>P0</b></font>",S["hc"])]],colWidths=[11*mm])
    badge.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),P0),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),2),("BOTTOMPADDING",(0,0),(-1,-1),2)]))
    head=Table([[badge,Paragraph("%s. %s"%(no,title),S["t"])]],colWidths=[13*mm,165*mm])
    head.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(-1,-1),0)]))
    out=[Paragraph("企业 AI 智能体中台 · 第一迭代 Sprint 任务卡 · v1.15",S["h"]),head,
         Paragraph("迭代目标："+goal,S["goal"]),Spacer(1,2*mm),tbl(rows,cw)]
    if note: out+=[Spacer(1,2*mm)]+bl(note)
    out.append(PageBreak()); return out

e=[]
# 35 选型决策框架
e+=sprint_card("35","模型选型决策框架","上线前堵住“LLM 万能”误区，让每个新需求先过选型评审。",
 [[Hc("任务"),Hc("产出"),Hc("工期"),Hc("验收")],
  [Paragraph("1 决策树建模",S["body"]),Paragraph("选型决策树（输入→确定性→可解释→成本）文档",S["body"]),Paragraph("2d",S["body"]),Paragraph("评审通过",S["body"])],
  [Paragraph("2 网关扩展",S["body"]),Paragraph("第25章网关新增 adapter_type{llm,ml_serving,rule}",S["body"]),Paragraph("3d",S["body"]),Paragraph("非LLM可注册",S["body"])],
  [Paragraph("3 向导嵌入",S["body"]),Paragraph("“新建智能体”强制填写选型说明",S["body"]),Paragraph("2d",S["body"]),Paragraph("缺选型不可发布",S["body"])],
  [Paragraph("4 对比看板",S["body"]),Paragraph("同任务 LLM vs ML 成本/准确率对比",S["body"]),Paragraph("2d",S["body"]),Paragraph("看板可用",S["body"])],
 ], [40*mm,78*mm,14*mm,46*mm],
 note=["依赖：第25章模型网关。高风险：团队习惯用LLM做一切，需培训+评审红线。"])

# 36 RAG 质量工程（最细化，按周）
e+=sprint_card("36","RAG 质量工程（重点）","把检索质量做成可度量、可门禁的工程能力，杜绝“漏召回→幻觉”。",
 [[Hc("周"),Hc("任务"),Hc("产出"),Hc("验收/门禁")],
  [Paragraph("W1",S["body"]),Paragraph("混合检索：BM25 + 向量，RRF 融合；父子文档切分；权限元数据落库",S["body"]),Paragraph("融合检索服务；切分流水线",S["body"]),Paragraph("双路召回可用",S["body"])],
  [Paragraph("W2",S["body"]),Paragraph("重排层：Top-100 → cross-encoder → Top-K；接入第25章网关",S["body"]),Paragraph("重排服务+配置",S["body"]),Paragraph("MRR 较纯向量↑",S["body"])],
  [Paragraph("W3",S["body"]),Paragraph("评测集：≥200 条 golden queries + 标准 doc_id；Recall@K/MRR/nDCG 脚本",S["body"]),Paragraph("评测集+自动化脚本",S["body"]),Paragraph("Recall@100≥0.95",S["body"])],
  [Paragraph("W4",S["body"]),Paragraph("质量门禁接入 CI；质量层与第18章合规层解耦；上线报告",S["body"]),Paragraph("CI 门禁+发布",S["body"]),Paragraph("门禁阻断生效",S["body"])],
 ], [12*mm,86*mm,52*mm,28*mm],
 note=["依赖：第5章知识库切分/元数据、第18章过滤点。","关键指标：Recall@100≥0.95 才放行；质量层失败不进合规层。","外部依据：2025-2026 混合检索+重排工程共识。"])

# 37 ML 级评测
e+=sprint_card("37","ML 级评测体系","用可靠评测替代 LLM-as-judge，给自动演化(第20章)立可信地基。",
 [[Hc("任务"),Hc("产出"),Hc("工期"),Hc("验收")],
  [Paragraph("1 客观校验",S["body"]),Paragraph("格式/工具参数/检索命中/引用/越权 5 类规则校验",S["body"]),Paragraph("3d",S["body"]),Paragraph("自动化通过",S["body"])],
  [Paragraph("2 黄金集",S["body"]),Paragraph("≥500 条多轮+边界对话标注",S["body"]),Paragraph("5d",S["body"]),Paragraph("覆盖边界",S["body"])],
  [Paragraph("3 judge 约束",S["body"]),Paragraph("LLM-as-judge 多模型交叉+校准阈值，仅辅助",S["body"]),Paragraph("2d",S["body"]),Paragraph("不单独定生死",S["body"])],
  [Paragraph("4 演化门禁",S["body"]),Paragraph("第20章演化建议接评测门禁",S["body"]),Paragraph("2d",S["body"]),Paragraph("不达标不灰度",S["body"])],
 ], [40*mm,78*mm,14*mm,46*mm],
 note=["依赖：第20章自动演化、第28章测试体系。","外部依据：ICLR2025+arXiv 2025/2026 LLM-as-judge 系统性偏差。"])

doc=SimpleDocTemplate(os.path.join(BASE,"P0三章_Sprint任务卡_A4.pdf"),pagesize=A4,
    leftMargin=16*mm,rightMargin=16*mm,topMargin=14*mm,bottomMargin=14*mm,title="P0 三章 Sprint 任务卡")
doc.build(e)
print("SAVED:",os.path.join(BASE,"P0三章_Sprint任务卡_A4.pdf"))
