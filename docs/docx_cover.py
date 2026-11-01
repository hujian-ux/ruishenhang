# -*- coding: utf-8 -*-
"""给 v1.15.docx 开头注入「立项评审封面」页（评审摘要 / 章节地图 / 关键决策点），不重跑全量生成。"""
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn

PATH = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34\企业AI智能体中台蓝图_v1.15.docx"

def set_ea(run):
    run.font.name = "Microsoft YaHei"
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn('w:eastAsia'), "Microsoft YaHei")

# 封面元素：按从上到下顺序
cover = [
    ("Title",   "企业 AI 智能体中台蓝图", None, True, None),
    ("Subtitle","v1.15 立项评审封面 · 平台工程 + 模型工程双闭环", 13, False, RGBColor(0x5b,0x71,0x85)),
    ("Empty",   "", None, False, None),
    ("Heading 2","评审摘要", None, True, None),
    ("Normal",  "本蓝图面向企业自建类 WorkBuddy 内部 AI 平台，提出「平台工程 + 模型工程」双闭环：以统一知识库、单一权限过滤点、自动演化与可插拔模型为底座，叠加经 AI 工程评审补强的模型工程十章（第 35–44 章）。共 44 章，其中 P0 优先项先行落地，确保权限、安全、选型、检索质量与可量化评测不打折扣。", None, False, None),
    ("Empty",   "", None, False, None),
    ("Heading 2","章节地图（44 章 · 4 大模块）", None, True, None),
    ("Normal",  "① 基础平台（第 1–18 章）：定位价值、总体架构、核心能力、技术选型、知识库与权限(P0)、数据安全(P0)、实施路线图、多部门场景、权限矩阵、部署清单、移动端设计与 PC/移动权限一致性。", None, False, None),
    ("Normal",  "② 启动与扩展（第 19–25 章）：启动期智能体与工具配置(P0)、平台自动演化(P1)、自助部门创建(P0)、外部工具对接(P0)、本地文件(P0)、UI 规范(P1)、大模型接入与计量计费(P0)。", None, False, None),
    ("Normal",  "③ 生产化与运营（第 26–34 章）：部署运维与可观测性(P0)、AI 行为安全(P0)、测试与评测(P0)、审计合规(P1)、运营推广(P1)、统一身份(P1)、多租户/集团隔离(P2)、法律与责任(P2)、性能与成本优化(P2)。", None, False, None),
    ("Normal",  "④ 模型工程补强（第 35–44 章）：模型选型(P0)、RAG 质量工程(P0)、ML 级评测(P0)、非 LLM 的 MLOps(P1)、幻觉抑制(P1)、成本与延迟优化(P1)、模型质量可观测(P1)、优雅降级(P2)、中文特化(P2)、Agent/工具层安全(P2)。", None, False, None),
    ("Empty",   "", None, False, None),
    ("Heading 2","关键决策点 / 立项红线", None, True, None),
    ("Normal",  "• 第 18 章单一过滤点不可绕过：任何读 / 写 / 外发 / 工具调用均走同一权限过滤，不因「系统自动」而放松。", None, False, None),
    ("Normal",  "• 第 35 章红线：缺选型结论的智能体不可发布（确定性任务默认非 LLM）。", None, False, None),
    ("Normal",  "• 第 36 章：检索质量层（找得全）与合规层（看不见）解耦，二者独立演进。", None, False, None),
    ("Normal",  "• 第 37 章：评测不依赖 LLM-as-judge，用客观指标 + 黄金集门禁驱动演化。", None, False, None),
    ("Normal",  "• 第 25 章：模型可插拔 + Token 计量计费，成本属敏感指标，受 scope 约束、跨部门仅管理员可见。", None, False, None),
    ("Normal",  "• 优先级：P0 先行（知识库权限、安全、选型、RAG、评测、可观测、AI 安全、启动配置、自助部门、工具对接、本地文件、模型接入）。", None, False, None),
]

doc = Document(PATH)
p0 = doc.paragraphs[0]                      # 原文档第一个段落
pb = p0.insert_paragraph_before()           # 封面与正文之间的分页
pb.add_run().add_break(WD_BREAK.PAGE)

for style, text, size, bold, color in reversed(cover):
    new_p = pb.insert_paragraph_before()
    if style == "Empty":
        continue
    try:
        new_p.style = doc.styles[style]
    except Exception:
        new_p.style = doc.styles["Normal"]
    run = new_p.add_run(text)
    set_ea(run)
    if size:
        run.font.size = Pt(size)
    if bold:
        run.bold = True
    if color is not None:
        run.font.color.rgb = color

doc.save(PATH)
print("SAVED with cover page:", PATH)
