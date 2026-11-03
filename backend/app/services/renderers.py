"""M17 · 多格式产出物渲染（Word / PPT / HTML / PDF）。

技能包的产出契约原本只有 xlsx，但真实交付场景里「给财务看的是表、给老板看的是 PPT、
盖章归档要 PDF、发群里要 HTML」。四种格式共用**同一个文档模型**，避免四套逻辑各写
一遍、字段互相漂移。

设计取舍：
1. **先归一，再渲染**。`build_model()` 把答案切成「章节 = 标题 + 正文 + 该章节的表格」，
   四个 renderer 只负责排版，不做解析 —— 解析错一次就在一处改。
   章节必须**自带表格**：早先版本把表全堆到文末，导致「二、量价分离」这种
   只有表没有正文的章节标题整段消失（表还在，但人看不出它属于哪一节）。
2. **中文字体必须显式设置**。docx 只设 font.name 只管西文，中文会退回默认宋体；
   pptx 的 `a:ea`、reportlab 的 CID 字体同理，否则标题变宋体、PDF 直接方框乱码。
3. **PDF 不依赖 Office/LibreOffice**。走 reportlab platypus 直接绘制，
   在纯 Python 环境下也能出件；字体优先本机微软雅黑，缺失回退 reportlab
   内置的 STSong-Light（CID 字体，不落地字体文件也不会乱码）。
4. **表格按页续排**，不截断也不缩到看不清：PPT 每页最多 8 行，超出另起一页。
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.artifact import _answer_paragraphs, extract_tables, strip_md as _strip_md

# 工业蓝，与前端主题、xlsx 表头同色
BRAND = "1F4E79"
BRAND_RGB = (0x1F, 0x4E, 0x79)
LIGHT = "EAF0F7"
GREY = "595959"

CJK_FONT = "等线"
PPT_ROWS_PER_SLIDE = 8          # PPT 单页表格行数上限（再多字就小到看不见）
PPT_BULLETS_PER_SLIDE = 6
_ORDINAL_RE = re.compile(r"^[一二三四五六七八九十\d]+[、.．]\s*")


# ────────────────────────── 文档模型 ──────────────────────────
@dataclass
class Section:
    heading: str
    paras: List[str] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def bullets(self) -> List[str]:
        """给 PPT 用：长段落按句号切成要点，避免一页塞一段 500 字的墙。"""
        out: List[str] = []
        for p in self.paras:
            if len(p) <= 90:
                out.append(p)
                continue
            out.extend(s.strip() for s in re.split(r"(?<=[。；!?])", p) if s.strip())
        return out


@dataclass
class DocModel:
    title: str
    subtitle: str
    meta: List[Tuple[str, str]] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)

    @property
    def tables(self) -> List[Dict[str, Any]]:
        return [t for s in self.sections for t in s.tables]


def _clean_title(t: str) -> str:
    return _strip_md(_ORDINAL_RE.sub("", (t or "").strip()).strip())


def _norm_table(t: Dict[str, Any], idx: int) -> Dict[str, Any]:
    return {"title": _clean_title(t.get("title") or "") or f"产出表{idx}",
            "headers": [_strip_md(str(h)) for h in t["headers"]],
            "rows": [[_strip_md(str(c)) for c in r] for r in t["rows"]]}


def _split_sections(answer: str) -> List[Section]:
    """按标题切章节，并把每张表挂到它所属标题下（按标题文本配对）。"""
    sections: List[Section] = []
    cur: Optional[Section] = None
    for kind, text in _answer_paragraphs(answer):
        if kind == "h":
            cur = Section(_clean_title(text))
            sections.append(cur)
        else:
            if cur is None:
                cur = Section("执行结论")
                sections.append(cur)
            cur.paras.append(_strip_md(text))

    by_head: Dict[str, Section] = {}
    for s in sections:
        by_head.setdefault(s.heading, s)

    for i, raw in enumerate(extract_tables(answer), 1):
        t = _norm_table(raw, i)
        sec = by_head.get(_clean_title(raw.get("title") or ""))
        if sec is None:
            # 表没有同名标题可挂（模型把表放在无序号段落之后）：归到「数据明细」
            sec = by_head.get("数据明细")
            if sec is None:
                sec = Section("数据明细")
                sections.append(sec)
                by_head["数据明细"] = sec
        sec.tables.append(t)

    kept = [s for s in sections if s.paras or s.tables]
    return kept or [Section("执行结论", [(answer or "").strip()[:4000] or "（模型未返回正文）"])]


def build_model(answer: str, meta: Dict[str, Any],
                input_summary: Optional[List[List[str]]] = None) -> DocModel:
    """把答案 + 元信息归一成渲染用的文档模型（四种格式共用）。"""
    agent = meta.get("agent_name") or meta.get("agent_id") or "技能包"
    files = meta.get("input_files") or []
    meta_rows = [
        ("技能包", agent),
        ("输入文件", "、".join(files) or "（无附件）"),
        ("模型", meta.get("model_id") or "-"),
        ("执行时间", meta.get("created_at_text") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("运行编号", meta.get("run_id") or "-"),
        ("追溯编号", meta.get("trace_id") or "-"),
        ("引用知识", "、".join(meta.get("citations") or []) or "（无）"),
    ]
    sections = _split_sections(answer or "")
    if not any(s.tables for s in sections) and input_summary and len(input_summary) > 1:
        sections.append(Section("输入数据摘要", [],
                                [_norm_table({"title": "输入数据摘要",
                                              "headers": input_summary[0],
                                              "rows": input_summary[1:]}, 0)]))
    return DocModel(title=agent,
                    subtitle="执行报告" if not files else f"执行报告 · {files[0]}",
                    meta=meta_rows, sections=sections)


def _pad(row: List[Any], n: int) -> List[Any]:
    """列数对齐：模型偶尔少写/多写一个单元格，直接写会让整张表错位。"""
    row = list(row)
    return row if len(row) == n else (row + [""] * n)[:n]


# ────────────────────────── Word ──────────────────────────
def build_docx(answer: str, meta: Dict[str, Any],
               input_summary: Optional[List[List[str]]] = None) -> bytes:
    """执行报告 Word：封面标题 + 元信息表 + 分章正文与表格。"""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    m = build_model(answer, meta, input_summary)
    doc = Document()

    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = CJK_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)   # 中文必须单独设

    def _cjk(run, size: float | None = None, bold: bool = False,
             color: str | None = None, white: bool = False):
        run.font.name = CJK_FONT
        run._element.rPr.rFonts.set(qn("w:eastAsia"), CJK_FONT)
        if size:
            run.font.size = Pt(size)
        run.bold = bold
        if white:
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        elif color:
            run.font.color.rgb = RGBColor.from_string(color)
        return run

    def _shade(cell, hexcolor: str):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), hexcolor)
        cell._tc.get_or_add_tcPr().append(shd)

    def _add_table(section_title: str, t: Dict[str, Any]):
        # 表格标题与章节同名时不重复打印，避免两行一样的字叠在一起
        if _clean_title(t["title"]) != section_title:
            tp = doc.add_paragraph()
            tp.paragraph_format.space_before = Pt(8)
            _cjk(tp.add_run(t["title"]), size=10.5, bold=True)
        ncol = max(1, len(t["headers"]))
        tb = doc.add_table(rows=1, cols=ncol)
        tb.style = "Table Grid"
        tb.alignment = WD_TABLE_ALIGNMENT.CENTER
        for j, h in enumerate(_pad(t["headers"], ncol)):
            cell = tb.rows[0].cells[j]
            _shade(cell, BRAND)
            cell.paragraphs[0].text = ""
            _cjk(cell.paragraphs[0].add_run(str(h)), size=9, bold=True, white=True)
        for row in t["rows"]:
            cells = tb.add_row().cells
            for j, val in enumerate(_pad(row, ncol)):
                cells[j].paragraphs[0].text = ""
                _cjk(cells[j].paragraphs[0].add_run(str(val)), size=9)

    # 封面
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _cjk(p.add_run(m.title), size=22, bold=True, color=BRAND)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _cjk(p2.add_run(m.subtitle), size=11, color=GREY)
    sep = doc.add_paragraph()
    sep.paragraph_format.space_after = Pt(6)
    _cjk(sep.add_run("─" * 46), size=8, color="C9D6E4")

    # 元信息表
    mt = doc.add_table(rows=len(m.meta), cols=2)
    mt.style = "Table Grid"
    mt.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, (k, v) in enumerate(m.meta):
        c0, c1 = mt.rows[i].cells
        c0.width, c1.width = Cm(3.2), Cm(13.0)
        _shade(c0, LIGHT)
        c0.paragraphs[0].text = ""
        _cjk(c0.paragraphs[0].add_run(k), size=9, bold=True, color=BRAND)
        c1.paragraphs[0].text = ""
        _cjk(c1.paragraphs[0].add_run(str(v)), size=9)

    doc.add_paragraph()

    # 正文章节（表格跟着自己的章节走）
    for s in m.sections:
        hp = doc.add_paragraph()
        hp.paragraph_format.space_before = Pt(10)
        hp.paragraph_format.space_after = Pt(4)
        _cjk(hp.add_run(s.heading), size=13, bold=True, color=BRAND)
        for text in s.paras:
            bp = doc.add_paragraph()
            bp.paragraph_format.first_line_indent = Cm(0.74)   # 中文首行缩进两字
            bp.paragraph_format.line_spacing = 1.4
            _cjk(bp.add_run(text), size=10.5)
        for t in s.tables:
            _add_table(s.heading, t)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ────────────────────────── PPT ──────────────────────────
def build_pptx(answer: str, meta: Dict[str, Any],
               input_summary: Optional[List[List[str]]] = None) -> bytes:
    """汇报 PPT：封面 → 每章一页要点 → 每张表分页 → 元信息尾页。"""
    from pptx import Presentation
    from pptx.dml.color import RGBColor as PRGB
    from pptx.enum.text import PP_ALIGN
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Inches, Pt

    m = build_model(answer, meta, input_summary)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W = prs.slide_width

    def _ea(run, name: str = CJK_FONT):
        """pptx 的 font.name 只设 a:latin，中文必须补 a:ea 才不吃默认字体。"""
        run.font.name = name
        rPr = run._r.get_or_add_rPr()
        for tag in ("a:ea", "a:cs"):
            el = rPr.find(qn(tag))
            if el is None:
                el = rPr.makeelement(qn(tag), {})
                rPr.append(el)
            el.set("typeface", name)

    def _text(slide, left, top, width, height, text, size=18, bold=False,
              color=(0x33, 0x33, 0x33), align=PP_ALIGN.LEFT, line_spacing=1.25):
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = align
        p.line_spacing = line_spacing
        r = p.add_run()
        r.text = text
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = PRGB(*color)
        _ea(r)
        return box

    def _bar(slide, top=Emu(0), height=Emu(1050000)):
        shp = slide.shapes.add_shape(1, Emu(0), top, W, height)   # 1 = 矩形
        shp.fill.solid()
        shp.fill.fore_color.rgb = PRGB(*BRAND_RGB)
        shp.line.fill.background()

    def _page_title(slide, text, kicker=""):
        _bar(slide)
        if kicker:
            _text(slide, Inches(0.7), Inches(0.36), Inches(8), Inches(0.35),
                  kicker, size=12, color=BRAND_RGB)
        _text(slide, Inches(0.7), Inches(0.72), Inches(11.6), Inches(0.6),
              text, size=26, bold=True, color=(0x1A, 0x1A, 0x1A))

    # 封面
    s = prs.slides.add_slide(blank)
    _bar(s, Emu(2200000), Emu(2200000))
    _text(s, Inches(0.9), Inches(2.3), Inches(11.5), Inches(1.0), m.title,
          size=40, bold=True, color=(0xFF, 0xFF, 0xFF))
    _text(s, Inches(0.9), Inches(3.35), Inches(11.5), Inches(0.5), m.subtitle,
          size=16, color=(0xD6, 0xE2, 0xF0))
    _text(s, Inches(0.9), Inches(5.6), Inches(11.5), Inches(0.4),
          f"{meta.get('model_id') or '-'} · {datetime.now().strftime('%Y-%m-%d')}",
          size=12, color=(0x8C, 0x9B, 0xAD))

    # 正文章节：一页要点（超长自动续页）。
    # 只有表没有正文的章节（如「二、量价分离」）不出要点页 —— 否则会得到一页
    # 只有标题、正文全空的幻灯片，讲的时候很尴尬；表格页会自己带标题。
    for s in m.sections:
        bullets = s.bullets
        if not bullets:
            continue
        pages = [bullets[i:i + PPT_BULLETS_PER_SLIDE]
                 for i in range(0, len(bullets), PPT_BULLETS_PER_SLIDE)]
        for pi, chunk in enumerate(pages):
            slide = prs.slides.add_slide(blank)
            _page_title(slide, s.heading + ("（续）" if pi else ""), "核心发现")
            box = slide.shapes.add_textbox(Inches(0.75), Inches(1.45),
                                           Inches(11.8), Inches(5.4))
            tf = box.text_frame
            tf.word_wrap = True
            for k, line in enumerate(chunk):
                p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                p.line_spacing = 1.35
                p.space_after = Pt(8)
                r = p.add_run()
                r.text = f"· {line}"
                r.font.size = Pt(17)
                r.font.color.rgb = PRGB(0x33, 0x33, 0x33)
                _ea(r)

    # 数据表：每张表按行分页
    for s in m.sections:
        for t in s.tables:
            rows, headers = t["rows"], t["headers"]
            pages = [rows[i:i + PPT_ROWS_PER_SLIDE]
                     for i in range(0, max(len(rows), 1), PPT_ROWS_PER_SLIDE)]
            for pi, chunk in enumerate(pages):
                slide = prs.slides.add_slide(blank)
                _page_title(slide, t["title"] + ("（续）" if pi else ""), "数据明细")
                nrow, ncol = len(chunk) + 1, max(1, len(headers))
                height = Inches(min(0.46 * nrow + 0.3, 5.4))
                tbl = slide.shapes.add_table(nrow, ncol, Inches(0.7), Inches(1.45),
                                             Inches(11.9), height).table
                for j, h in enumerate(_pad(headers, ncol)):
                    c = tbl.cell(0, j)
                    c.text = ""
                    c.fill.solid()
                    c.fill.fore_color.rgb = PRGB(*BRAND_RGB)
                    r = c.text_frame.paragraphs[0].add_run()
                    r.text = str(h)
                    r.font.size = Pt(12)
                    r.font.bold = True
                    r.font.color.rgb = PRGB(0xFF, 0xFF, 0xFF)
                    _ea(r)
                for i, row in enumerate(chunk, 1):
                    for j, v in enumerate(_pad(row, ncol)):
                        c = tbl.cell(i, j)
                        c.text = ""
                        c.fill.solid()
                        c.fill.fore_color.rgb = (PRGB(0xFF, 0xFF, 0xFF) if i % 2
                                                 else PRGB(0xEE, 0xF3, 0xF8))
                        r = c.text_frame.paragraphs[0].add_run()
                        r.text = str(v)
                        r.font.size = Pt(11)
                        r.font.color.rgb = PRGB(0x33, 0x33, 0x33)
                        _ea(r)
                if len(rows) > PPT_ROWS_PER_SLIDE:
                    _text(slide, Inches(0.7), Inches(7.0), Inches(11.9), Inches(0.3),
                          f"第 {pi + 1}/{len(pages)} 页 · 共 {len(rows)} 行",
                          size=10, color=(0x8C, 0x9B, 0xAD))

    # 尾页：可追溯信息
    s = prs.slides.add_slide(blank)
    _page_title(s, "执行信息", "可追溯")
    box = s.shapes.add_textbox(Inches(0.75), Inches(1.5), Inches(11.5), Inches(5.2))
    tf = box.text_frame
    tf.word_wrap = True
    for k, (key, val) in enumerate(m.meta):
        p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
        p.line_spacing = 1.4
        r1 = p.add_run()
        r1.text = f"{key}："
        r1.font.size = Pt(15)
        r1.font.bold = True
        r1.font.color.rgb = PRGB(*BRAND_RGB)
        _ea(r1)
        r2 = p.add_run()
        r2.text = str(val)
        r2.font.size = Pt(15)
        r2.font.color.rgb = PRGB(0x33, 0x33, 0x33)
        _ea(r2)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ────────────────────────── HTML ──────────────────────────
def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_html(answer: str, meta: Dict[str, Any],
               input_summary: Optional[List[List[str]]] = None) -> str:
    """单文件 HTML 报告：内嵌样式，可直接发群/归档，也可用浏览器打印成 PDF。"""
    m = build_model(answer, meta, input_summary)

    meta_html = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in m.meta)

    body: List[str] = []
    for s in m.sections:
        body.append(f"<section><h2>{_esc(s.heading)}</h2>")
        for text in s.paras:
            body.append(f"<p>{_esc(text)}</p>")
        for t in s.tables:
            ncol = max(1, len(t["headers"]))
            head = "".join(f"<th>{_esc(h)}</th>" for h in t["headers"])
            rows = "".join(
                "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in _pad(r, ncol)) + "</tr>"
                for r in t["rows"])
            title = "" if t["title"] == s.heading else f"<h3>{_esc(t['title'])}</h3>"
            body.append(
                f'<div class="tbl">{title}<div class="scroll"><table>'
                f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
                f'<p class="cap">共 {len(t["rows"])} 行 · {ncol} 列</p></div>')
        body.append("</section>")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(m.title)} · {_esc(m.subtitle)}</title>
<style>
:root{{--brand:#{BRAND};--light:#{LIGHT};--grey:{GREY};}}
*{{box-sizing:border-box}}
body{{margin:0;background:#F4F6F9;color:#222;
 font-family:"等线","Microsoft YaHei","PingFang SC",-apple-system,sans-serif;
 line-height:1.7;-webkit-font-smoothing:antialiased}}
.page{{max-width:980px;margin:0 auto;padding:0 0 60px;background:#fff;
 box-shadow:0 1px 3px rgba(16,36,64,.08)}}
header{{background:var(--brand);color:#fff;padding:38px 44px 30px}}
header .kicker{{font-size:12px;letter-spacing:.18em;opacity:.72;text-transform:uppercase}}
header h1{{margin:10px 0 6px;font-size:30px;font-weight:600;letter-spacing:.5px}}
header .sub{{font-size:14px;opacity:.85}}
main{{padding:8px 44px 0}}
h2{{font-size:18px;color:var(--brand);margin:34px 0 12px;padding-left:11px;
 border-left:4px solid var(--brand);line-height:1.3}}
h3{{font-size:14px;color:#16283C;margin:0 0 10px;font-weight:600}}
p{{margin:0 0 12px;font-size:14.5px;text-align:justify}}
p.cap{{font-size:11px;color:var(--grey);margin:6px 0 0}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff}}
thead th{{background:var(--brand);color:#fff;font-weight:600;text-align:left;
 padding:9px 12px;border:1px solid #1A4263;white-space:nowrap}}
tbody td{{padding:8px 12px;border:1px solid #DDE3EA;vertical-align:top}}
tbody tr:nth-child(even){{background:#F7F9FC}}
.scroll{{overflow-x:auto}}
div.tbl{{margin:0 0 24px}}
table.info{{font-size:13px;margin:26px 0 0}}
table.info th{{width:104px;background:var(--light);color:var(--brand);
 border:1px solid #DDE3EA;padding:8px 12px;text-align:left;font-weight:600;white-space:nowrap}}
table.info td{{border:1px solid #DDE3EA;padding:8px 12px;color:#33404F;word-break:break-all}}
footer{{margin:34px 44px 0;padding-top:14px;border-top:1px solid #E6EBF1;
 font-size:11.5px;color:var(--grey);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}}
@media print{{
 body{{background:#fff}} .page{{box-shadow:none;max-width:none}}
 header{{padding:24px 0 16px;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
 main,footer{{padding-left:0;padding-right:0;margin-left:0;margin-right:0}}
 section{{break-inside:avoid}} h2{{break-after:avoid}}
 thead{{display:table-header-group}}
}}
</style></head>
<body><div class="page">
<header>
  <div class="kicker">Enterprise AI Platform · Skill Execution</div>
  <h1>{_esc(m.title)}</h1>
  <div class="sub">{_esc(m.subtitle)}</div>
</header>
<main>
{''.join(body)}
<table class="info">{meta_html}</table>
</main>
<footer><span>本报告由企业 AI 智能体中台自动生成，结论需人工确认后对外。</span>
<span>{datetime.now().strftime('%Y-%m-%d %H:%M')}</span></footer>
</div></body></html>"""


# ────────────────────────── PDF ──────────────────────────
_PDF_FONT_CACHE: Dict[str, str] = {}


def _register_pdf_fonts() -> Tuple[str, str]:
    """注册中文字体，返回 (正文字体名, 标题字体名)。

    优先本机字体（有真粗体），否则用 reportlab 内置 CID 字体 STSong-Light ——
    后者不落地任何字体文件，跨机器部署不会因为缺字体变成一堆方框。
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    if _PDF_FONT_CACHE.get("body"):
        return _PDF_FONT_CACHE["body"], _PDF_FONT_CACHE["bold"]

    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name, reg, bold in [
        ("YaHei", os.path.join(win, "msyh.ttc"), os.path.join(win, "msyhbd.ttc")),
        ("SimHei", os.path.join(win, "simhei.ttf"), None),
        ("SimSun", os.path.join(win, "simsun.ttc"), None),
    ]:
        if not os.path.exists(reg):
            continue
        try:
            kw = {"subfontIndex": 0} if reg.endswith(".ttc") else {}
            pdfmetrics.registerFont(TTFont(name, reg, **kw))
            bold_name = name
            if bold and os.path.exists(bold):
                try:
                    pdfmetrics.registerFont(
                        TTFont(name + "-Bold", bold, subfontIndex=0)
                        if bold.endswith(".ttc") else TTFont(name + "-Bold", bold))
                    bold_name = name + "-Bold"
                except Exception:
                    bold_name = name
            _PDF_FONT_CACHE.update(body=name, bold=bold_name)
            return name, bold_name
        except Exception:
            continue

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:
        pass
    _PDF_FONT_CACHE.update(body="STSong-Light", bold="STSong-Light")
    return "STSong-Light", "STSong-Light"


def build_pdf(answer: str, meta: Dict[str, Any],
              input_summary: Optional[List[List[str]]] = None) -> bytes:
    """PDF 报告：A4 纵向，标题条 + 元信息 + 分章正文 + 自动分页长表。"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (HRFlowable, LongTable, Paragraph,
                                    SimpleDocTemplate, Spacer, TableStyle)

    body_font, bold_font = _register_pdf_fonts()
    m = build_model(answer, meta, input_summary)

    def P(text: str, size: float = 9.5, color="#222222", bold: bool = False,
          leading: float | None = None, align=TA_JUSTIFY) -> Paragraph:
        return Paragraph(
            _esc(text),
            ParagraphStyle("p", fontName=bold_font if bold else body_font,
                           fontSize=size, leading=leading or size * 1.7,
                           textColor=colors.HexColor(color), alignment=align,
                           wordWrap="CJK"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"{m.title} {m.subtitle}", author="企业 AI 智能体中台")
    story: List[Any] = [P(m.title, size=20, color="#" + BRAND, bold=True, leading=26, align=TA_LEFT),
                        P(m.subtitle, size=9.5, color="#" + GREY, leading=14, align=TA_LEFT),
                        Spacer(1, 4),
                        HRFlowable(width="100%", thickness=1.6,
                                   color=colors.HexColor("#" + BRAND), spaceAfter=8)]

    meta_data = [[Paragraph(_esc(k), ParagraphStyle("mk", fontName=bold_font, fontSize=8.5,
                                                   textColor=colors.HexColor("#" + BRAND))),
                  Paragraph(_esc(v), ParagraphStyle("mv", fontName=body_font, fontSize=8.5,
                                                    wordWrap="CJK",
                                                    textColor=colors.HexColor("#333333")))]
                 for k, v in m.meta]
    mt = LongTable(meta_data, colWidths=[26 * mm, None])
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#" + LIGHT)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3EA")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story += [mt, Spacer(1, 10)]

    usable = A4[0] - 36 * mm
    for s in m.sections:
        story.append(Spacer(1, 8))
        story.append(P(s.heading, size=12.5, color="#" + BRAND, bold=True,
                       leading=18, align=TA_LEFT))
        story.append(Spacer(1, 3))
        for text in s.paras:
            story.append(P(text, size=9.5))
            story.append(Spacer(1, 2))

        for t in s.tables:
            story.append(Spacer(1, 6))
            if t["title"] != s.heading:
                story.append(P(t["title"], size=10, bold=True, color="#16283C",
                               leading=14, align=TA_LEFT))
                story.append(Spacer(1, 3))
            ncol = max(1, len(t["headers"]))
            # 列宽按「该列最长内容字符数」分配，最短 14mm 保底，
            # 否则「金额」这种窄列会被挤成一条线、备注列又撑满整页。
            raw = [max(max([len(str(t["headers"][j]))] +
                           [len(_pad(r, ncol)[j]) for r in t["rows"][:40]] or [4]), 4)
                   for j in range(ncol)]
            total = float(sum(raw))
            widths = [max(14 * mm, usable * w / total) for w in raw]
            scale = usable / sum(widths)
            widths = [w * scale for w in widths]

            cell_p = ParagraphStyle("tc", fontName=body_font, fontSize=7.6, leading=10.5,
                                    wordWrap="CJK", textColor=colors.HexColor("#22303F"))
            head_p = ParagraphStyle("th", fontName=bold_font, fontSize=7.8, leading=10.5,
                                    wordWrap="CJK", textColor=colors.white)
            data = [[Paragraph(_esc(h), head_p) for h in _pad(t["headers"], ncol)]]
            data += [[Paragraph(_esc(c), cell_p) for c in _pad(r, ncol)] for r in t["rows"]]
            tb = LongTable(data, colWidths=widths, repeatRows=1)
            tb.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + BRAND)),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C9D3DF")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#F6F8FB")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("TOPPADDING", (0, 0), (-1, -1), 2.6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
            ]))
            story.append(tb)

    story += [Spacer(1, 14),
              HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#DDE3EA")),
              P("本报告由企业 AI 智能体中台自动生成，结论需人工确认后对外。",
                size=8, color="#" + GREY, align=TA_LEFT)]
    doc.build(story)
    return buf.getvalue()
