# -*- coding: utf-8 -*-
"""蓝图 HTML -> Word(.docx)
- 标题/段落/列表/表格照常转换
- 内嵌 SVG 图（架构图、权限数据流向图、部门入驻流程图）用 Pillow 自渲染为 PNG 嵌入
- <div class="pb"> 触发分页
"""
import os, re, io, math
from html.parser import HTMLParser
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.oxml.ns import qn
from docx.enum.text import WD_BREAK
from PIL import Image, ImageDraw, ImageFont

HTML_PATH = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34\企业AI智能体中台蓝图.html"
OUT_PATH = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34\企业AI智能体中台蓝图_v1.15.docx"

EA_FONT = "Microsoft YaHei"
FONT_PATHS = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

def set_eastasia(style):
    style.font.name = EA_FONT
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn('w:eastAsia'), EA_FONT)

# ---------------- SVG -> PNG (subset renderer) ----------------
def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def parse_attrs(s):
    d = {}
    for k, v in re.findall(r'(\w[\w-]*)\s*=\s*"([^"]*)"', s):
        d[k] = v
    return d

_SVG_FONT = None
def get_font(sz):
    global _SVG_FONT
    if _SVG_FONT is None:
        for p in FONT_PATHS:
            if os.path.exists(p):
                _SVG_FONT = p
                break
    try:
        return ImageFont.truetype(_SVG_FONT, int(sz)) if _SVG_FONT else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def render_svg(svg, out_png, scale=2):
    vb = re.search(r'viewBox="([\d.\s]+)"', svg)
    if vb:
        nums = [float(x) for x in vb.group(1).split()]
        vw, vh = nums[2], nums[3]
    else:
        w = re.search(r'width="([\d.]+)"', svg)
        h = re.search(r'height="([\d.]+)"', svg)
        vw = float(w.group(1)) if w else 680.0
        vh = float(h.group(1)) if h else 400.0
    W, H = int(vw * scale), int(vh * scale)
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)

    # rects
    for m in re.finditer(r'<rect\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x = float(a.get('x', 0)) * scale
        y = float(a.get('y', 0)) * scale
        w = float(a.get('width', 0)) * scale
        h = float(a.get('height', 0)) * scale
        rx = float(a.get('rx', 0)) * scale
        fill = a.get('fill', 'none')
        stroke = a.get('stroke')
        sw = float(a.get('stroke-width', 1)) * scale
        fcol = hex2rgb(fill) if fill.startswith('#') else ((10, 37, 64) if fill.startswith('url') else None)
        scol = hex2rgb(stroke) if (stroke and stroke.startswith('#')) else None
        try:
            d.rounded_rectangle([x, y, x + w, y + h], radius=rx,
                                fill=fcol, outline=scol,
                                width=(int(sw) if scol else 0))
        except Exception:
            d.rectangle([x, y, x + w, y + h], fill=fcol, outline=scol,
                        width=(int(sw) if scol else 0))

    # lines (+ arrowheads)
    for m in re.finditer(r'<line\b([^>]*)/>', svg):
        a = parse_attrs(m.group(1))
        x1 = float(a['x1']) * scale
        y1 = float(a['y1']) * scale
        x2 = float(a['x2']) * scale
        y2 = float(a['y2']) * scale
        stroke = a.get('stroke', '#000')
        sw = float(a.get('stroke-width', 1)) * scale
        col = hex2rgb(stroke) if stroke.startswith('#') else (0, 0, 0)
        d.line([(x1, y1), (x2, y2)], fill=col, width=max(1, int(sw)))
        if 'marker-end' in a:
            ang = math.atan2(y2 - y1, x2 - x1)
            L = 11 * scale
            a1 = ang + math.radians(150)
            a2 = ang - math.radians(150)
            p1 = (x2 + L * math.cos(a1), y2 + L * math.sin(a1))
            p2 = (x2 + L * math.cos(a2), y2 + L * math.sin(a2))
            d.line([(x2, y2), p1], fill=col, width=max(1, int(sw)))
            d.line([(x2, y2), p2], fill=col, width=max(1, int(sw)))

    # texts
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', svg, re.S):
        a = parse_attrs(m.group(1))
        content = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not content:
            continue
        x = float(a.get('x', 0)) * scale
        y = float(a.get('y', 0)) * scale
        sz = float(a.get('font-size', 12))
        fill = a.get('fill', '#000')
        col = hex2rgb(fill) if fill.startswith('#') else (0, 0, 0)
        anc = a.get('text-anchor', 'start')
        fnt = get_font(sz)
        anchor = 'ma' if anc == 'middle' else 'la'
        d.text((x, y), content, font=fnt, fill=col, anchor=anchor)

    img.save(out_png, 'PNG')
    return out_png

# ---------------- HTML -> DOCX ----------------
class Conv(HTMLParser):
    def __init__(self, doc, svg_blocks):
        super().__init__(convert_charrefs=True)
        self.doc = doc
        self.ap = None
        self.bold = False
        self.skip = 0
        self.in_table = False
        self.in_cell = False
        self.rows = []
        self.cur_row = []
        self.cell_text = []
        self.cell_header = False
        self.pre = False
        self.div_h_open = False
        self.last_div = None
        self.svg_blocks = svg_blocks
        self.svg_index = 0

    def add_run(self, text):
        if self.ap is None:
            self.ap = self.doc.add_paragraph()
        r = self.ap.add_run(text)
        if self.bold:
            r.bold = True
        if self.pre:
            r.font.name = "Consolas"
            r.font.size = Pt(9)
            r._element.rPr.rFonts.set(qn('w:eastAsia'), "Consolas")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get('class', '')
        if tag == 'svg':
            self.skip += 1
            if self.svg_index < len(self.svg_blocks):
                try:
                    png = render_svg(self.svg_blocks[self.svg_index],
                                    OUT_PATH + f".svg{self.svg_index}.png")
                    self.doc.add_picture(png, width=Inches(6.0))
                    cap = a.get('aria-label', '图示')
                    cp = self.doc.add_paragraph()
                    cr = cp.add_run(f"▲ 图示：{cap}")
                    cr.italic = True
                    cr.font.size = Pt(9)
                    cr.font.color.rgb = RGBColor(0x5b, 0x71, 0x85)
                    os.remove(png)
                except Exception as e:
                    self.ap = self.doc.add_paragraph()
                    r = self.ap.add_run(f"【图示：{a.get('aria-label','图示')} —— 详见 HTML 版蓝图】")
                    r.italic = True
                    r.font.color.rgb = RGBColor(0x5b, 0x71, 0x85)
                self.svg_index += 1
            self.ap = None
            return
        if tag == 'div' and cls == 'pb':
            p = self.doc.add_paragraph()
            run = p.add_run()
            run.add_break(WD_BREAK.PAGE)
            return
        if self.skip > 0:
            return
        if tag == 'div' and 'deptcard' in cls:
            self.doc.add_paragraph()
            self.ap = None
            return
        if tag == 'div' and 'rolecol' in cls:
            self.doc.add_paragraph()
            self.ap = None
            return
        if tag == 'div' and 'dn' in cls:
            self.ap = self.doc.add_paragraph()
            self.bold = True
            self.last_div = 'dn'
            return
        if tag == 'div' and 'dp' in cls:
            self.ap = self.doc.add_paragraph()
            self.last_div = 'dp'
            return
        if tag == 'h1':
            self.ap = self.doc.add_heading(level=0)
        elif tag == 'h2':
            self.ap = self.doc.add_heading(level=1)
        elif tag == 'h3':
            self.ap = self.doc.add_heading(level=2)
        elif tag == 'h4':
            self.ap = self.doc.add_paragraph()
            self.bold = True
        elif tag == 'p':
            self.ap = self.doc.add_paragraph()
        elif tag == 'li':
            self.ap = self.doc.add_paragraph(style='List Bullet')
        elif tag in ('b', 'strong'):
            self.bold = True
        elif tag == 'br':
            if self.ap:
                self.ap.add_run().add_break()
        elif tag == 'pre':
            self.pre = True
            self.ap = self.doc.add_paragraph()
        elif tag == 'table':
            self.in_table = True
            self.rows = []
        elif tag == 'tr':
            self.cur_row = []
        elif tag in ('td', 'th'):
            self.in_cell = True
            self.cell_header = (tag == 'th')
            self.cell_text = []
        elif tag == 'div' and 'h' == cls.strip():
            self.div_h_open = True
            if self.ap is None:
                self.ap = self.doc.add_paragraph()

    def handle_endtag(self, tag):
        if tag == 'svg':
            self.skip = max(0, self.skip - 1)
            if self.skip == 0:
                self.ap = None
            return
        if self.skip > 0:
            return
        if tag in ('h1', 'h2', 'h3', 'p', 'li', 'pre'):
            self.ap = None
            self.pre = False
        elif tag in ('b', 'strong'):
            self.bold = False
        elif tag == 'table':
            self._build_table()
            self.in_table = False
        elif tag == 'tr':
            self.rows.append(self.cur_row)
        elif tag in ('td', 'th'):
            self.in_cell = False
            self.cur_row.append((''.join(self.cell_text), self.cell_header))
        elif tag == 'h4':
            self.bold = False
            self.ap = None
        elif tag == 'div' and self.div_h_open:
            if self.ap:
                self.ap.add_run().add_break()
            self.div_h_open = False
        elif tag == 'div' and self.last_div in ('dn', 'dp'):
            self.bold = False
            self.ap = None
            self.last_div = None

    def handle_data(self, data):
        if self.skip > 0:
            return
        if self.in_cell:
            self.cell_text.append(data)
            return
        if not data.strip():
            return
        if self.ap is None:
            self.ap = self.doc.add_paragraph()
        self.add_run(data)

    def _build_table(self):
        if not self.rows:
            return
        ncol = max((len(r) for r in self.rows), default=0)
        if ncol == 0:
            return
        t = self.doc.add_table(rows=len(self.rows), cols=ncol)
        try:
            t.style = 'Table Grid'
        except Exception:
            pass
        for i, row in enumerate(self.rows):
            for j in range(ncol):
                cell = t.cell(i, j)
                text, hdr = row[j] if j < len(row) else ('', False)
                cell.text = text
                if hdr:
                    for p in cell.paragraphs:
                        for r in p.runs:
                            r.bold = True

def main():
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()
    svg_blocks = re.findall(r'<svg\b.*?</svg>', html, re.S)
    doc = Document()
    set_eastasia(doc.styles['Normal'])
    for sname in ('Title', 'Heading 1', 'Heading 2', 'Heading 3'):
        try:
            set_eastasia(doc.styles[sname])
        except Exception:
            pass
    conv = Conv(doc, svg_blocks)
    conv.feed(html)
    doc.save(OUT_PATH)
    print("SAVED:", OUT_PATH)
    print("paragraphs:", len(doc.paragraphs), "tables:", len(doc.tables),
          "images:", len(doc.inline_shapes))

if __name__ == '__main__':
    main()
