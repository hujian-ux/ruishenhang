# -*- coding: utf-8 -*-
"""用 reportlab 直接生成 A4 PDF（不依赖 Word/LibreOffice）：
 1) 角色权限速查卡_A4.pdf   —— 员工/部门管理员/平台管理员 三栏对照
 2) 九部门落地卡片_A4.pdf   —— 9 部门 3x3 紧凑卡片（单页）
 3) 九部门落地卡片_拆分.pdf —— 每部门单独一页（9 页）
中文：msyh.ttc(常规) / msyhbd.ttc(粗体)
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

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
RED = colors.HexColor("#c62828")
LINE = colors.HexColor("#d7e0ea")
HEADBG = colors.HexColor("#1565c0")
CARDBG = colors.HexColor("#eef4fb")

def styles():
    return {
        "title": ParagraphStyle("title", fontName="YaHei-Bold", fontSize=16,
                                textColor=NAVY, alignment=TA_CENTER, leading=20),
        "sub": ParagraphStyle("sub", fontName="YaHei", fontSize=9.5,
                              textColor=MUTED, alignment=TA_CENTER, leading=13),
        "hcell": ParagraphStyle("hcell", fontName="YaHei-Bold", fontSize=11,
                                textColor=colors.white, leading=14),
        "hcellsub": ParagraphStyle("hcellsub", fontName="YaHei", fontSize=7.5,
                                   textColor=colors.white, leading=10),
        "body": ParagraphStyle("body", fontName="YaHei", fontSize=8,
                               textColor=INK, leading=10.5, alignment=TA_LEFT),
        "bodyb": ParagraphStyle("bodyb", fontName="YaHei-Bold", fontSize=8,
                                textColor=NAVY, leading=10.5),
        "note": ParagraphStyle("note", fontName="YaHei", fontSize=8,
                               textColor=MUTED, leading=11),
        "dtitle": ParagraphStyle("dtitle", fontName="YaHei-Bold", fontSize=18,
                                 textColor=colors.white, leading=22),
        "dpos": ParagraphStyle("dpos", fontName="YaHei", fontSize=10,
                               textColor=colors.white, leading=13),
        "lbl": ParagraphStyle("lbl", fontName="YaHei-Bold", fontSize=10,
                              textColor=BLUE, leading=14),
        "val": ParagraphStyle("val", fontName="YaHei", fontSize=10,
                              textColor=INK, leading=14),
    }

def build_refcard_pdf(path, S):
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm,
                            title="角色权限速查卡")
    e = []
    e.append(Paragraph("角色权限速查卡", S["title"]))
    e.append(Paragraph("企业 AI 智能体中台 · 一页 A4 · 随账号下发 / 张贴", S["sub"]))
    e.append(Spacer(1, 6*mm))

    roles = [
        ("普通员工", [
            ("能看到", "全域公开知识（制度/产品手册）＋ 本部门知识 ＋ 个人知识。"),
            ("能做", "使用通用 Agent（问答/写作/分析等）；新建/编辑个人知识；读本部门知识。"),
            ("不能做", "看其他部门空间；管理部门 Agent；配权限；看审计日志。"),
            ("典型", "各部门一线员工、实习生。"),
        ]),
        ("部门管理员", [
            ("能看到", "全域只读 ＋ 本部门全部 ＋（授权）其他部门。"),
            ("能做", "写部门知识；管理部门 Agent/工作流；审批本部门共享申请；维护本部门知识库。"),
            ("不能做", "看其他未授权部门；配平台权限；部门入驻审批；看全公司审计。"),
            ("典型", "部门负责人 / 指定的部门运营。"),
        ]),
        ("平台管理员", [
            ("能看到", "全部（全域/部门/个人）。"),
            ("能做", "平台与模型网关配置；RBAC 权限；部门入驻审批；跨部门共享配置；审计日志；用量看板。"),
            ("不能做", "无（最高权限，但操作受审计约束，敏感删除需二次确认）。"),
            ("典型", "IT / 数字化团队。"),
        ]),
    ]

    header = [Paragraph(n, S["hcell"]) for n, _ in roles]
    bodies = []
    for _, items in roles:
        parts = []
        for label, txt in items:
            parts.append('<font name="YaHei-Bold" color="#0a2540">%s：</font>%s' % (label, txt))
        bodies.append(Paragraph("<br/>".join(parts), S["body"]))

    t = Table([header, bodies], colWidths=[60*mm]*3, rowHeights=[11*mm, 78*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), HEADBG),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("GRID", (0,0), (-1,-1), 0.6, LINE),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("BACKGROUND", (0,1), (-1,1), colors.white),
    ]))
    e.append(t)
    e.append(Spacer(1, 5*mm))
    e.append(Paragraph(
        "越权防护铁律：所有检索在召回阶段按 scope / owner / dept 过滤；"
        "跨部门仅经“共享空间”或 access_list 白名单开通并审计。如遇权限异常，联系平台管理员。",
        S["note"]))
    doc.build(e)
    print("SAVED:", path)

DEPTS = [
    ("行政", "企业制度的“总服务台”",
     "制度/通知/资产信息分散，员工反复问；会议/资产靠人工。",
     "制度问答 Agent、会议纪要自动生成、资产台账查询、用印/差旅流程问答。",
     "制度手册、SOP、会务模板、资产台账。",
     "全域(制度公开) + 行政库(内部流程) + 个人。",
     "① 导入制度手册/SOP ② 配制度问答 Agent ③ 梳理资产台账模板。",
     "制度类重复咨询↓%、会议纪要生成耗时↓。"),
    ("人事 / HR", "招聘与员工体验中枢",
     "招聘/入职/考勤/培训重复解答，薪酬/员工信息敏感。",
     "JD 与面试题生成、入职引导问答、培训材料生成、假期/报销制度问答。",
     "招聘话术、培训课件、组织架构、人事制度。",
     "HR 库(薪酬/员工信息机密) + 全域(通用制度)。",
     "① 导入人事制度与培训课件 ② 建招聘话术 Agent ③ 设 HR 库机密标签。",
     "招聘初筛效率↑、入职引导自助率↑。"),
    ("销售", "一线获客与转化引擎",
     "产品/报价/话术版本多，找资料慢。",
     "产品知识问答、销售话术助手、报价单与方案初稿、客户画像分析。",
     "产品手册、报价模板、成功案例、竞品分析。",
     "销售库 + 全域(产品) + 个人(客户跟进)。",
     "① 导入产品手册与报价模板 ② 配销售话术助手 ③ 规范客户跟进个人库。",
     "方案初稿生成时长↓、资料查找耗时↓。"),
    ("设计", "品牌与视觉规范守护者",
     "设计规范/素材/品牌规范散落。",
     "设计规范问答、文案版式初稿、素材检索、评审清单。",
     "品牌规范、设计模板、组件库、视觉标准。",
     "设计库 + 全域(品牌公开部分)。",
     "① 导入品牌规范与组件库 ② 配设计规范问答 ③ 建素材检索 Agent。",
     "设计返工率↓、素材查找耗时↓。"),
    ("跟单", "订单履约与交期把控",
     "订单进度/交期/异常跨系统查，口头传递易错。",
     "订单进度问答(对接 ERP)、交期预警、异常自动汇总、跟单日报。",
     "跟单 SOP、交期规则、异常处理模板。",
     "跟单库 + ERP 数据(按角色) + 个人(在途订单)。",
     "① 建跟单 SOP 与交期规则 ② 对接 ERP 读取订单 ③ 配跟单日报 Agent。",
     "异常响应时长↓、跟单日报生成耗时↓。"),
    ("研发", "技术与知识沉淀中枢",
     "技术文档/需求/接口散，知识易流失。",
     "技术文档问答、需求与测试用例生成、代码评审辅助、知识沉淀检索。",
     "技术方案、接口文档、复盘、研发规范。",
     "研发库(部分机密) + 个人(在途)。",
     "① 导入研发规范与接口文档 ② 配技术文档问答 ③ 建代码评审辅助 Agent。",
     "文档查找耗时↓、需求初稿生成时长↓。"),
    ("推广 / 市场", "获客内容与投放中枢",
     "文案/投放/活动素材重复度高。",
     "营销文案生成、活动方案初稿、投放数据解读、社媒内容批量生成。",
     "投放策略、素材库、活动复盘、渠道手册。",
     "推广库 + 全域(品牌) + 个人。",
     "① 导入渠道手册与素材库 ② 配营销文案 Agent ③ 建活动方案初稿流。",
     "文案产出量↑、活动方案初稿时长↓。"),
    ("财务", "合规与资金管控",
     "报销/对账/报表重复劳动。",
     "报销政策问答、票据与对账自动化、报表生成。",
     "财务制度、科目说明、报表模板。",
     "财务库(机密) + 全域(报销制度公开)。",
     "① 导入财务制度与报表模板 ② 配报销政策问答 ③ 设财务库机密标签。",
     "报销咨询自助率↑、对账自动化率↑。"),
    ("客服", "客户问题第一响应",
     "重复问答、知识更新慢。",
     "智能客服问答、工单摘要、话术推荐。",
     "常见问题、服务手册、客诉 SOP。",
     "客服库 + 全域(产品)。",
     "① 导入服务手册与常见问题 ② 配智能客服问答 ③ 建工单摘要 Agent。",
     "自助解决率↑、工单处理时长↓。"),
]

def dept_body_para(d, S):
    name, pos, pain, use, know, perm, first, kpi = d
    lines = [
        ("痛点", pain), ("用途", use), ("贡献", know),
        ("权限", perm), ("首周", first), ("指标", kpi),
    ]
    parts = []
    for label, txt in lines:
        parts.append('<font name="YaHei-Bold" color="#0a2540">%s：</font>%s' % (label, txt))
    return Paragraph("<br/>".join(parts), S["body"])

def dept_header_para(d, S):
    name, pos, _, _, _, _, _, _ = d
    return Paragraph('%s&nbsp;&nbsp;<font size="7.5">%s</font>' % (name, pos), S["hcell"])

def build_deptcards_pdf(path, S, split=False):
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm,
                            title="九部门落地卡片")
    e = []
    if not split:
        e.append(Paragraph("九部门落地卡片（速查）", S["title"]))
        e.append(Paragraph("企业 AI 智能体中台 · 一套底座、各部门各有空间", S["sub"]))
        e.append(Spacer(1, 6*mm))
        n = len(DEPTS)
        rows = (n + 2) // 3
        data = [["", "", ""] for _ in range(rows * 2)]
        for idx, d in enumerate(DEPTS):
            r = idx // 3
            c = idx % 3
            data[r*2][c] = dept_header_para(d, S)
            data[r*2+1][c] = dept_body_para(d, S)
        t = Table(data, colWidths=[60*mm]*3, rowHeights=[11*mm, 60*mm]*rows)
        style = [
            ("BACKGROUND", (0,0), (-1,0), HEADBG),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("GRID", (0,0), (-1,-1), 0.6, LINE),
            ("LEFTPADDING", (0,0), (-1,-1), 5),
            ("RIGHTPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("BACKGROUND", (0,1), (-1,-1), colors.white),
        ]
        # header rows are even rows
        for r in range(rows):
            style.append(("BACKGROUND", (0, r*2), (-1, r*2), HEADBG))
        t.setStyle(TableStyle(style))
        e.append(t)
        e.append(Spacer(1, 4*mm))
        e.append(Paragraph(
            "说明：所有部门复用同一套通用 Agent（知识问答/文档写作/数据分析/流程自动化/合规审核），"
            "差异只在“部门知识库 + 部门专属 Agent/工作流”。入驻按《部门入驻 SOP》推进。",
            S["note"]))
    else:
        for i, d in enumerate(DEPTS):
            name, pos, pain, use, know, perm, first, kpi = d
            head = Table([[Paragraph(name, S["dtitle"])],
                          [Paragraph(pos, S["dpos"])]], colWidths=[180*mm])
            head.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), HEADBG),
                ("LEFTPADDING", (0,0), (-1,-1), 10),
                ("RIGHTPADDING", (0,0), (-1,-1), 10),
                ("TOPPADDING", (0,0), (0,0), 10),
                ("BOTTOMPADDING", (0,0), (0,0), 2),
                ("TOPPADDING", (0,1), (0,1), 0),
                ("BOTTOMPADDING", (0,1), (0,1), 10),
            ]))
            rows = [
                ("痛点", pain), ("用途", use), ("贡献知识", know),
                ("权限特征", perm), ("首周动作", first), ("关键指标", kpi),
            ]
            data = [[Paragraph(lbl, S["lbl"]), Paragraph(val, S["val"])] for lbl, val in rows]
            body = Table(data, colWidths=[30*mm, 150*mm])
            body.setStyle(TableStyle([
                ("VALIGN", (0,0), (-1,-1), "TOP"),
                ("GRID", (0,0), (-1,-1), 0.6, LINE),
                ("LEFTPADDING", (0,0), (-1,-1), 6),
                ("RIGHTPADDING", (0,0), (-1,-1), 6),
                ("TOPPADDING", (0,0), (-1,-1), 7),
                ("BOTTOMPADDING", (0,0), (-1,-1), 7),
                ("BACKGROUND", (0,0), (0,-1), CARDBG),
            ]))
            block = [head, Spacer(1, 4*mm), body]
            if i < len(DEPTS) - 1:
                block.append(PageBreak())
            e.append(KeepTogether(block))
    doc.build(e)
    print("SAVED:", path)

if __name__ == "__main__":
    S = styles()
    build_refcard_pdf(os.path.join(BASE, "角色权限速查卡_A4.pdf"), S)
    build_deptcards_pdf(os.path.join(BASE, "九部门落地卡片_A4.pdf"), S, split=False)
    build_deptcards_pdf(os.path.join(BASE, "九部门落地卡片_拆分.pdf"), S, split=True)
