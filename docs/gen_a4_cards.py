# -*- coding: utf-8 -*-
"""生成两份独立 A4 单页 Word：
 1) 角色权限速查卡_A4.docx  —— 员工/部门管理员/平台管理员 三栏对照
 2) 九部门落地卡片_A4.docx  —— 9 部门 3x3 紧凑卡片
页面尺寸 A4(210x297mm)，边距 15mm；正文用 Microsoft YaHei。
可在 Word 内「另存为 PDF」直接下发。
"""
from docx import Document
from docx.shared import Pt, Mm, RGBColor
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

EA = "Microsoft YaHei"
NAVY = RGBColor(0x0a, 0x25, 0x40)
BLUE = RGBColor(0x15, 0x65, 0xc0)
INK = RGBColor(0x1f, 0x2d, 0x3d)
MUTED = RGBColor(0x5b, 0x71, 0x85)
RED = RGBColor(0xc6, 0x28, 0x28)

def set_ea(style_or_run, font=EA):
    style_or_run.font.name = font
    rpr = style_or_run.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn('w:eastAsia'), font)

def setup_a4(doc):
    sec = doc.sections[0]
    sec.page_width = Mm(210)
    sec.page_height = Mm(297)
    sec.top_margin = Mm(15)
    sec.bottom_margin = Mm(15)
    sec.left_margin = Mm(15)
    sec.right_margin = Mm(15)
    set_ea(doc.styles['Normal'])
    doc.styles['Normal'].font.size = Pt(9)

def title(doc, text, sub=None):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(16)
    r.font.color.rgb = NAVY
    set_ea(r)
    if sub:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r2 = p2.add_run(sub)
        r2.font.size = Pt(9.5)
        r2.font.color.rgb = MUTED
        set_ea(r2)

def cell_run(cell, text, *, bold=False, color=INK, size=8.5, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    if align:
        p.alignment = align
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.color.rgb = color
    set_ea(r)
    return p

def add_line(cell, text, *, bold=False, color=INK, size=8.5, prefix="• "):
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(1.5)
    r = p.add_run(prefix + text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.color.rgb = color
    set_ea(r)

# ---------------------------------------------------------------- 1) 速查卡
def build_refcard(path):
    doc = Document()
    setup_a4(doc)
    title(doc, "角色权限速查卡", "企业 AI 智能体中台 · 一页 A4 · 随账号下发 / 张贴")
    doc.add_paragraph()

    roles = [
        ("普通员工", BLUE, [
            ("能看到", "全域公开知识（制度/产品手册）＋ 本部门知识 ＋ 个人知识。"),
            ("能做", "使用通用 Agent（问答/写作/分析等）；新建/编辑个人知识；读本部门知识。"),
            ("不能做", "看其他部门空间；管理部门 Agent；配权限；看审计日志。"),
            ("典型", "各部门一线员工、实习生。"),
        ]),
        ("部门管理员", BLUE, [
            ("能看到", "全域只读 ＋ 本部门全部 ＋（授权）其他部门。"),
            ("能做", "写部门知识；管理部门 Agent/工作流；审批本部门共享申请；维护本部门知识库。"),
            ("不能做", "看其他未授权部门；配平台权限；部门入驻审批；看全公司审计。"),
            ("典型", "部门负责人 / 指定的部门运营。"),
        ]),
        ("平台管理员", BLUE, [
            ("能看到", "全部（全域/部门/个人）。"),
            ("能做", "平台与模型网关配置；RBAC 权限；部门入驻审批；跨部门共享配置；审计日志；用量看板。"),
            ("不能做", "无（最高权限，但操作受审计约束，敏感删除需二次确认）。"),
            ("典型", "IT / 数字化团队。"),
        ]),
    ]

    t = doc.add_table(rows=1, cols=3)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, (name, _, _) in enumerate(roles):
        cell_run(hdr[i], name, bold=True, color=RGBColor(0xff, 0xff, 0xff), size=12,
                align=WD_ALIGN_PARAGRAPH.CENTER)
        # header fill
        from docx.oxml.ns import qn as _qn
        tcPr = hdr[i]._tc.get_or_add_tcPr()
        shd = tcPr.makeelement(_qn('w:shd'), {_qn('w:fill'): "1565C0", _qn('w:val'): "clear"})
        tcPr.append(shd)

    main = t.add_row().cells
    for i, (_, _, items) in enumerate(roles):
        c = main[i]
        c.text = ""
        for label, txt in items:
            p = c.add_paragraph()
            p.paragraph_format.space_after = Pt(3)
            rl = p.add_run(label + "：")
            rl.bold = True
            rl.font.size = Pt(8.5)
            rl.font.color.rgb = NAVY
            set_ea(rl)
            rt = p.add_run(txt)
            rt.font.size = Pt(8.5)
            rt.font.color.rgb = INK
            set_ea(rt)

    doc.add_paragraph()
    note = doc.add_paragraph()
    rn = note.add_run("越权防护铁律：所有检索在召回阶段按 scope / owner / dept 过滤；"
                      "跨部门仅经\"共享空间\"或 access_list 白名单开通并审计。如遇权限异常，联系平台管理员。")
    rn.italic = True
    rn.font.size = Pt(8.5)
    rn.font.color.rgb = MUTED
    set_ea(rn)

    doc.save(path)
    print("SAVED:", path)

# ---------------------------------------------------------------- 2) 九部门卡片
DEPTS = [
    ("行政", "企业制度的\"总服务台\"",
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

def build_deptcards(path):
    doc = Document()
    setup_a4(doc)
    title(doc, "九部门落地卡片（速查）", "企业 AI 智能体中台 · 一套底座、各部门各有空间")
    doc.add_paragraph()

    n = len(DEPTS)
    rows = (n + 2) // 3
    t = doc.add_table(rows=rows * 2, cols=3)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER

    for idx, (name, pos, pain, use, know, perm, first, kpi) in enumerate(DEPTS):
        r = idx // 3
        c = idx % 3
        hcell = t.cell(r * 2, c)
        bcell = t.cell(r * 2 + 1, c)
        # header cell: 部门名 + 定位
        hcell.text = ""
        p = hcell.paragraphs[0]
        rn = p.add_run(name)
        rn.bold = True
        rn.font.size = Pt(10)
        rn.font.color.rgb = RGBColor(0xff, 0xff, 0xff)
        set_ea(rn)
        rp = p.add_run("　" + pos)
        rp.font.size = Pt(7.5)
        rp.font.color.rgb = RGBColor(0xff, 0xff, 0xff)
        set_ea(rp)
        from docx.oxml.ns import qn as _qn
        tcPr = hcell._tc.get_or_add_tcPr()
        shd = tcPr.makeelement(_qn('w:shd'), {_qn('w:fill'): "1565C0", _qn('w:val'): "clear"})
        tcPr.append(shd)
        # body cell
        bcell.text = ""
        add_line(bcell, pain, prefix="痛点：", bold=False)
        add_line(bcell, use, prefix="用途：", bold=False)
        add_line(bcell, know, prefix="贡献：", bold=False)
        add_line(bcell, perm, prefix="权限：", bold=False)
        add_line(bcell, first, prefix="首周：", bold=False)
        add_line(bcell, kpi, prefix="指标：", bold=False)

    # 剩余空单元格清空（若 n%3!=0）
    for idx in range(n, rows * 3):
        r = idx // 3
        c = idx % 3
        t.cell(r * 2, c).text = ""
        t.cell(r * 2 + 1, c).text = ""

    doc.add_paragraph()
    note = doc.add_paragraph()
    rn = note.add_run("说明：所有部门复用同一套通用 Agent（知识问答/文档写作/数据分析/流程自动化/合规审核），"
                      "差异只在\"部门知识库 + 部门专属 Agent/工作流\"。入驻按《部门入驻 SOP》推进。")
    rn.italic = True
    rn.font.size = Pt(8)
    rn.font.color.rgb = MUTED
    set_ea(rn)

    doc.save(path)
    print("SAVED:", path)

if __name__ == '__main__':
    build_refcard(r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34\角色权限速查卡_A4.docx")
    build_deptcards(r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34\九部门落地卡片_A4.docx")
