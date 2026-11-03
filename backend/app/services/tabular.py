"""M16 · 表格解析（P0 文件进出的「进」这一步）。

技能包的输入绝大多数是 xlsx（费用台账、销售流水、考勤表），而 LLM 只能吃文本。
本模块负责把表格压成**可执行的文本上下文**，关键约束：

1. **不许把整张表塞进 prompt**。5000 行 × 20 列会直接打爆上下文且烧钱。
   策略：表头 + 列画像（类型/空值率/唯一值数）+ 前 N 行样本 + 行数声明。
2. **解析失败必须降级，不许抛异常**。上传一份奇怪的文件不该让 invoke 500 ——
   返回 `{"ok": False, "error": ...}`，调用方把错误写进 prompt 让模型知道数据不可用。
3. **结构要给全，数据可截断**。模型判断问题靠的是「有哪些列、什么类型、
   有没有异常值」，不是靠看完每一行。

支持的格式：xlsx/xlsm（openpyxl）、csv/tsv、txt/md/json（按纯文本截断）。
"""
from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, List, Optional

# 单个 sheet 采样行数（进 prompt 的样本量）
SAMPLE_ROWS = 80
# 进入 prompt 的总字符上限（超了按 sheet 依次截断）
PROMPT_CHAR_BUDGET = 7000
# 唯一值统计最多扫这么多行（避免大表耗时）
UNIQUE_SCAN_LIMIT = 2000
# 数据预览接口返回的行数
PREVIEW_ROWS = 20

XLSX_EXT = {"xlsx", "xlsm"}
CSV_EXT = {"csv", "tsv"}
TEXT_EXT = {"txt", "md", "markdown", "json", "log", "yml", "yaml", "sql"}


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _cell_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _infer_type(values: List[str]) -> str:
    """列类型推断：只做 int/float/date/text/empty 五档，够模型判断口径。"""
    vals = [v for v in values if v != ""]
    if not vals:
        return "empty"
    num = 0
    for v in vals:
        try:
            float(v.replace(",", "").replace("%", ""))
            num += 1
        except (ValueError, AttributeError):
            pass
    if num == len(vals):
        return "number"
    # 日期粗判（2026-11-03 / 2026/11/3 / 11月3日）
    date_like = sum(1 for v in vals if ("-" in v or "/" in v or "月" in v) and any(c.isdigit() for c in v))
    if date_like >= len(vals) * 0.8:
        return "date"
    return "text"


def _profile_column(header: str, col_values: List[str]) -> Dict[str, Any]:
    total = len(col_values)
    empties = sum(1 for v in col_values if v == "")
    uniq = set(v for v in col_values if v != "")
    return {
        "name": header,
        "type": _infer_type(col_values),
        "empty_rate": round(empties / total, 3) if total else 0.0,
        "unique": len(uniq),
        "sample_values": list(uniq)[:5],
    }


def _header_row(rows: List[List[str]]) -> int:
    """找表头行：前 5 行里第一个「非空单元格最多且含非数字」的行。

    真实台账前面常有一两行标题/单位说明，直接取第 1 行会把「2026年6月费用台账」
    当成 20 个列名中的第一个，后面全错位。
    """
    best, best_score = 0, -1
    for i, row in enumerate(rows[:5]):
        cells = [c for c in row if c != ""]
        if len(cells) < 2:
            continue
        texty = sum(1 for c in cells if _infer_type([c]) == "text")
        score = len(cells) + texty * 0.5
        if score > best_score:
            best, best_score = i, score
    return best


def _sheet_from_rows(name: str, rows: List[List[str]]) -> Dict[str, Any]:
    """把二维表 -> 结构化轮廓。"""
    rows = [r for r in rows if any(c != "" for c in r)]
    if not rows:
        return {"name": name, "rows": 0, "cols": 0, "headers": [], "columns": [],
                "sample": [], "empty": True}

    h_idx = _header_row(rows)
    headers = rows[h_idx]
    # 表头去重去空：空列名补 col{i}，重名加后缀，否则模型无法引用
    seen: Dict[str, int] = {}
    norm_headers: List[str] = []
    for i, h in enumerate(headers):
        h = (h or "").strip() or f"col{i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        norm_headers.append(h)

    data = rows[h_idx + 1:]
    width = len(norm_headers)
    data = [list(r) + [""] * (width - len(r)) if len(r) < width else r[:width] for r in data]

    columns = []
    for ci, h in enumerate(norm_headers):
        col_values = [r[ci] for r in data[:UNIQUE_SCAN_LIMIT]]
        columns.append(_profile_column(h, col_values))

    return {
        "name": name,
        "rows": len(data),
        "cols": width,
        "headers": norm_headers,
        "columns": columns,
        "sample": data[:SAMPLE_ROWS],
        "empty": False,
        "all_rows": data,
    }


def _parse_xlsx(raw: bytes) -> List[Dict[str, Any]]:
    from openpyxl import load_workbook  # 延迟导入：未装 openpyxl 时其他格式仍可用

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    out = []
    try:
        for ws in wb.worksheets:
            rows = [[_cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)]
            out.append(_sheet_from_rows(ws.title, rows))
    finally:
        wb.close()
    return out


def _parse_csv(raw: bytes, name: str) -> List[Dict[str, Any]]:
    # 编码兜底：UTF-8-SIG → GBK（国内 Excel 导出的 CSV 常是 GBK）
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 无法解码（尝试 UTF-8/GBK 均失败）")
    delim = "\t" if name.lower().endswith(".tsv") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [[c for c in r] for r in reader]
    return [_sheet_from_rows(os.path.splitext(name)[0] or "Sheet1", rows)]


def _parse_text(raw: bytes, name: str) -> List[Dict[str, Any]]:
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("文本文件无法解码")
    lines = text.splitlines()
    return [{
        "name": os.path.splitext(name)[0] or "文本",
        "rows": len(lines),
        "cols": 1,
        "headers": ["内容"],
        "columns": [{"name": "内容", "type": "text", "empty_rate": 0.0,
                     "unique": len(set(lines)), "sample_values": []}],
        "sample": [[ln] for ln in lines[:SAMPLE_ROWS]],
        "empty": False,
        "all_rows": [[ln] for ln in lines],
        "plain_text": True,
    }]


def parse(raw: bytes, filename: str) -> Dict[str, Any]:
    """解析文件 -> 结构化轮廓。**任何异常都收敛为 ok=False**，不向上抛。"""
    ext = ext_of(filename)
    try:
        if ext in XLSX_EXT:
            sheets = _parse_xlsx(raw)
            kind = "xlsx"
        elif ext in CSV_EXT:
            sheets = _parse_csv(raw, filename)
            kind = "csv"
        elif ext in TEXT_EXT:
            sheets = _parse_text(raw, filename)
            kind = "text"
        else:
            return {"ok": False, "kind": ext or "unknown", "filename": filename,
                    "error": f"不支持的文件类型：.{ext}（支持 xlsx/xlsm/csv/tsv/txt/md/json）"}
    except ImportError:
        return {"ok": False, "kind": ext, "filename": filename,
                "error": "缺少 openpyxl，无法解析表格（pip install openpyxl）"}
    except Exception as exc:  # 损坏文件、加密文件、超限等一律降级
        return {"ok": False, "kind": ext, "filename": filename,
                "error": f"解析失败：{type(exc).__name__}: {exc}"[:200]}

    if not sheets:
        return {"ok": False, "kind": kind, "filename": filename, "error": "文件内没有可用工作表"}

    return {
        "ok": True,
        "kind": kind,
        "filename": filename,
        "sheet_count": len(sheets),
        "total_rows": sum(s["rows"] for s in sheets),
        "sheets": sheets,
    }


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    def cell(v: str) -> str:
        return (v or "").replace("|", "／").replace("\n", " ").strip()
    out = ["| " + " | ".join(cell(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(out)


def to_prompt_context(parsed: Dict[str, Any], budget: int = PROMPT_CHAR_BUDGET) -> str:
    """把解析结果压成给 LLM 的文本。这是「文件进 prompt」的唯一入口。

    顺序：文件概览 → 每 sheet 的列画像 + 样本表。超预算时先砍样本行，
    绝不砍列画像（模型靠列画像判断口径）。
    """
    if not parsed.get("ok"):
        return (f"【附件不可用】{parsed.get('filename', '未知文件')}：{parsed.get('error', '解析失败')}\n"
                f"请向用户说明该文件无法读取，并列出需要他补充的字段，不要臆造数据。")

    lines: List[str] = [
        f"【用户提供的附件】{parsed['filename']}"
        f"（{parsed['kind']}，共 {parsed['sheet_count']} 个工作表，{parsed['total_rows']} 行数据）"
    ]
    for sh in parsed["sheets"]:
        if sh.get("empty"):
            lines.append(f"\n── 工作表「{sh['name']}」：空表")
            continue
        lines.append(f"\n── 工作表「{sh['name']}」：{sh['rows']} 行 × {sh['cols']} 列")
        # 列画像（不占多少 token，信息密度最高）
        prof = []
        for c in sh["columns"]:
            p = f"{c['name']}({c['type']}"
            if c["empty_rate"]:
                p += f",空{c['empty_rate']:.0%}"
            if c["type"] != "number" and c["unique"] <= 30:
                p += f",取值{c['unique']}种"
            p += ")"
            prof.append(p)
        lines.append("列画像：" + "、".join(prof))

        if sh.get("plain_text"):
            body = "\n".join(r[0] for r in sh["sample"])
            lines.append("内容前若干行：\n" + body)
        else:
            avail = len(sh["sample"])
            lines.append(f"前 {avail} 行样本：\n" + _md_table(sh["headers"], sh["sample"]))
            if sh["rows"] > avail:
                lines.append(f"（以上为样本，实际共 {sh['rows']} 行；如需逐行核算请说明取数口径）")

        if sum(len(x) for x in lines) > budget:
            lines.append("\n（后续工作表因篇幅省略——如需请指明具体工作表名）")
            break

    text = "\n".join(lines)
    if len(text) > budget:
        text = text[:budget] + "\n（内容过长已截断）"
    return text


def to_preview(parsed: Dict[str, Any], rows: int = PREVIEW_ROWS) -> Dict[str, Any]:
    """给前端的结构预览：不含全量数据，只回表头 + 前 N 行。"""
    if not parsed.get("ok"):
        return {"ok": False, "error": parsed.get("error"), "filename": parsed.get("filename")}
    return {
        "ok": True,
        "filename": parsed["filename"],
        "kind": parsed["kind"],
        "sheet_count": parsed["sheet_count"],
        "total_rows": parsed["total_rows"],
        "sheets": [{
            "name": s["name"], "rows": s["rows"], "cols": s["cols"],
            "headers": s["headers"],
            "columns": s["columns"],
            "preview": s["sample"][:rows],
        } for s in parsed["sheets"]],
    }


def to_raw_sheet(parsed: Dict[str, Any], limit: int = 2000) -> Optional[Dict[str, Any]]:
    """产出物里附「输入数据」页用：单 sheet 且行数不大才附原始数据。

    多 sheet / 大表不附 —— 产出物不该变成输入文件的副本。
    """
    if not parsed.get("ok") or parsed["sheet_count"] != 1:
        return None
    sh = parsed["sheets"][0]
    if sh.get("empty") or sh["rows"] > limit:
        return None
    return {"name": sh["name"], "headers": sh["headers"],
            "rows": sh["all_rows"][:limit], "truncated": sh["rows"] > limit}


def summarize_for_sheet(parsed: Dict[str, Any]) -> List[List[str]]:
    """产出物「输入数据摘要」页的行内容。"""
    rows: List[List[str]] = [["文件", "工作表", "行数", "列数", "列名（类型）"]]
    if not parsed.get("ok"):
        rows.append([parsed.get("filename", ""), "-", "0", "0", f"解析失败：{parsed.get('error', '')}"])
        return rows
    for sh in parsed["sheets"]:
        cols = "、".join(f"{c['name']}({c['type']})" for c in sh["columns"])
        rows.append([parsed["filename"], sh["name"], str(sh["rows"]), str(sh["cols"]), cols])
    return rows


def dumps_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
