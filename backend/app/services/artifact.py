"""M16 · 产出物生成（P0 文件进出的「出」这一步）。

技能包的输出契约是 xlsx（费用分析表、复盘打分表、JD 拆解表），所以 invoke 的
结果不能只有一段文字。本模块把 LLM 的答案转成可下载的 xlsx，规则：

1. **永远有产出物**。哪怕模型只说了几句话，也生成「执行结论」页——否则
   「下载产出物」按钮会时有时无，用户无法预期。
2. **能认出的结构化内容都还原成表**。答案里的 markdown 表格、```json 数组
   各占一个 sheet，sheet 名取表前最近的小标题（技能包习惯用 `## 五、产出` 这类）。
3. **可溯源**。附「输入数据摘要」页（文件名/sheet/行列数/列名类型）与元信息
   （哪个技能包、什么模型、哪次运行、trace_id），这是审计要的，也是用户回头
   核对的入口。
4. **不复制输入**。只有单 sheet 且行数不大才附原始数据，避免产出物变成输入副本。
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

SHEET_NAME_MAX = 31
ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]\:\*\?\/\\]")

# 表前最近的小标题（## / ### / **加粗** / 数字编号）→ 用作 sheet 名
HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*(.+?)\s*$|\*\*(.+?)\*\*\s*$)")
SEP_ROW_RE = re.compile(r"^\s*\|?[\s:\-\|]+\|?\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
FENCE_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def _clean_sheet_name(name: str, fallback: str) -> str:
    n = ILLEGAL_SHEET_CHARS.sub("", (name or "").strip())
    n = re.sub(r"^[一二三四五六七八九十\d]+[、.．]\s*", "", n)   # 去掉「五、」这类序号
    n = n.strip() or fallback
    return n[:SHEET_NAME_MAX]


def _split_cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def extract_tables(answer: str) -> List[Dict[str, Any]]:
    """从答案里抽结构化表：markdown 表格 + ```json 数组。按出现顺序返回。"""
    tables: List[Dict[str, Any]] = []
    lines = answer.splitlines()
    last_heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        m = HEADING_RE.match(line)
        if m:
            last_heading = (m.group(1) or m.group(2) or "").strip()
            i += 1
            continue

        # markdown 表格：连续 |...| 行，第二行必须是分隔行
        if TABLE_ROW_RE.match(line) and i + 1 < len(lines) and SEP_ROW_RE.match(lines[i + 1]):
            headers = _split_cells(line)
            rows: List[List[str]] = []
            j = i + 2
            while j < len(lines) and TABLE_ROW_RE.match(lines[j]):
                rows.append(_split_cells(lines[j]))
                j += 1
            if headers and rows:
                tables.append({"title": last_heading, "headers": headers, "rows": rows,
                               "kind": "markdown"})
            i = j
            continue
        i += 1

    # ```json 数组（模型常直接吐结构化结果）
    for blk in FENCE_JSON_RE.findall(answer):
        try:
            import json
            data = json.loads(blk)
        except Exception:
            continue
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            headers: List[str] = []
            for x in data:
                for k in x:
                    if k not in headers:
                        headers.append(k)
            rows = [[str(x.get(h, "")) for h in headers] for x in data]
            tables.append({"title": "", "headers": headers, "rows": rows, "kind": "json"})
    return tables


def _answer_paragraphs(answer: str) -> List[Tuple[str, str]]:
    """答案正文切成 (类型, 文本)：h=标题 / p=段落。表格行不进正文（已在 sheet 里）。"""
    out: List[Tuple[str, str]] = []
    in_fence = False
    for raw in answer.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if TABLE_ROW_RE.match(line):
            continue
        if not line.strip():
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append(("h", (m.group(1) or m.group(2) or "").strip()))
        else:
            out.append(("p", line.strip()))
    return out


def _autosize(ws, max_width: int = 46, min_width: int = 9) -> None:
    """按内容估列宽。中文按 2 个字符宽算，否则 xlsx 里中文列会挤成一坨。"""
    from openpyxl.utils import get_column_letter
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        widest = 0
        for c in col[:80]:           # 只扫前 80 行，大表不为此耗时
            v = "" if c.value is None else str(c.value)
            w = sum(2 if ord(ch) > 127 else 1 for ch in v)
            widest = max(widest, w)
        ws.column_dimensions[letter].width = max(min_width, min(max_width, widest + 2))


def _write_table_sheet(wb, title: str, headers: List[str], rows: List[List[str]],
                       fallback: str, font_name: str = "等线") -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(_clean_sheet_name(title, fallback))
    ws.append(headers)
    head_fill = PatternFill("solid", fgColor="1F4E79")   # 工业蓝，对齐前端主题
    thin = Side(style="thin", color="D0D7E2")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", size=10, name=font_name)
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
    for r in rows:
        # 列数对齐（模型偶尔少写一个单元格，直接 append 会让表错位）
        fixed = (r + [""] * len(headers))[:len(headers)]
        ws.append(fixed)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = border
            c.font = Font(size=10, name=font_name)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    _autosize(ws)


def _write_conclusion_sheet(wb, answer: str, meta: Dict[str, Any],
                            font_name: str = "等线") -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    ws = wb.create_sheet("执行结论")
    blue = Font(bold=True, color="1F4E79", size=11, name=font_name)
    ws.append(["项目", "内容"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", size=10, name=font_name)
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center")
    info = [
        ("技能包", meta.get("agent_name") or meta.get("agent_id") or "-"),
        ("输入文件", "、".join(meta.get("input_files") or []) or "（无附件）"),
        ("模型", meta.get("model_id") or "-"),
        ("执行时间", meta.get("created_at_text") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("运行编号", meta.get("run_id") or "-"),
        ("追溯编号", meta.get("trace_id") or "-"),
        ("引用知识", "、".join(meta.get("citations") or []) or "（无）"),
    ]
    for k, v in info:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 60
    for row in ws.iter_rows(min_row=2):
        row[0].font = blue
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

    # 正文：空一行后按段落铺在 B 列，保住可读性
    ws.append([])
    ws.append(["分析正文"])
    ws.cell(row=ws.max_row, column=1).font = blue
    for kind, text in _answer_paragraphs(answer):
        ws.append(["", text])
        cell = ws.cell(row=ws.max_row, column=2)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(bold=(kind == "h"), size=10 if kind == "p" else 11,
                         color="1F4E79" if kind == "h" else "000000", name=font_name)
    if not _answer_paragraphs(answer):
        ws.append(["", answer.strip()[:2000] or "（模型未返回正文）"])


def build_xlsx(answer: str, meta: Dict[str, Any],
               input_summary: Optional[List[List[str]]] = None,
               raw_sheet: Optional[Dict[str, Any]] = None) -> bytes:
    """生成产出物 xlsx 字节流。**任何情况下都返回可用文件**。

    sheet 顺序：执行结论 → 各结构化表 → 输入数据摘要 →（可选）输入数据。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)          # 不要空的默认 sheet

    _write_conclusion_sheet(wb, answer or "", meta)

    tables = extract_tables(answer or "")
    used: Dict[str, int] = {}
    for idx, t in enumerate(tables, 1):
        base = _clean_sheet_name(t["title"], f"产出表{idx}")
        # 同名 sheet 加序号，否则 openpyxl 会自作聪明改名+弹警告
        if base in used:
            used[base] += 1
            base = _clean_sheet_name(f"{base}{used[base]}", f"产出表{idx}")
        else:
            used[base] = 0
        _write_table_sheet(wb, base, t["headers"], t["rows"], f"产出表{idx}")

    if input_summary:
        _write_table_sheet(wb, "输入数据摘要", input_summary[0], input_summary[1:], "输入数据摘要")

    if raw_sheet:
        rows = raw_sheet["rows"]
        title = f"输入数据_{raw_sheet['name']}"
        _write_table_sheet(wb, title, raw_sheet["headers"], rows, "输入数据")
        if raw_sheet.get("truncated"):
            ws = wb[wb.sheetnames[-1]]
            ws.append([f"（数据超过上限，仅附前 {len(rows)} 行）"] + [""] * (len(raw_sheet["headers"]) - 1))

    # 没有任何数据 sheet 时，至少保证「执行结论」在
    if not wb.sheetnames:
        ws = wb.create_sheet("执行结论")
        ws["A1"] = "（本次执行未产生可结构化结果）"
        ws["A1"].font = Font(bold=True)
        ws["A1"].fill = PatternFill("solid", fgColor="FFF2CC")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def artifact_name(agent_name: str, source_name: str = "") -> str:
    """产出物文件名：`技能包名_源文件名_20261103-2210.xlsx`（去非法字符）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    a = re.sub(r"[\\/:*?\"<>|\s]+", "", agent_name or "产出物")[:24] or "产出物"
    s = re.sub(r"[\\/:*?\"<>|\s]+", "", (source_name or "").rsplit(".", 1)[0])[:24]
    return f"{a}_{s}_{stamp}.xlsx" if s else f"{a}_{stamp}.xlsx"
