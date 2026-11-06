"""把客户原始制度文件（Word / Excel / PDF）批量转成平台能导入的纯文本。

为什么需要：M8 的 `ingest_corpus.py` 只接受 TEXT_EXT | HTML_EXT，
客户手上的制度几乎都是 .docx / .xlsx / .pdf，直接丢进语料目录会被判
「不支持的扩展名」。这一层把格式差异挡在导入之前。

用法（小白不用碰，见 corpus_inbox/README.md）：
    .venv/Scripts/python.exe scripts/convert_corpus.py --init      # 只建标准目录
    .venv/Scripts/python.exe scripts/convert_corpus.py             # 转换（默认路径）
    .venv/Scripts/python.exe scripts/convert_corpus.py --dry-run   # 只看会转什么
    .venv/Scripts/python.exe scripts/convert_corpus.py --force     # 覆盖已有 txt

保留原始相对目录结构（global/ dept_finance/ …），输出同名 .txt（UTF-8）。
文件名会成为知识库里的文档标题，所以**不改文件名**。
单个文件失败不中断整批；失败清单写进报告，方便逐条补。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "corpus_inbox" / "raw"
DEFAULT_OUT = ROOT / "corpus_inbox" / "converted"
REPORT = Path(__file__).resolve().parents[1] / "reports" / "corpus_convert.json"

DEPT_DIRS = ["global", "dept_finance", "dept_sales", "dept_hr"]
PLACEHOLDER = "PLACE_FILES_HERE.txt"
PASS_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log",
            ".yml", ".yaml", ".sql", ".html", ".htm"}
CONVERT_EXT = {".docx", ".xlsx", ".pdf"}
# 老格式给出可执行的建议，而不是一句「不支持」
LEGACY_EXT = {".doc": "请用 Word 打开另存为 .docx",
              ".xls": "请用 Excel 打开另存为 .xlsx",
              ".ppt": "请另存为 .pptx 后再转文字（当前不导入演示文稿）",
              ".pptx": "演示文稿暂不导入，请另存为 PDF 或 Word"}


class ConvertError(Exception):
    """单篇转换失败（记入报告，不中断整批）。"""


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ConvertError("无法解码（非 UTF-8 / GBK 文本）")


def _docx_text(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []
    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            parts.append(t)
    for i, table in enumerate(doc.tables, 1):
        parts.append(f"[表格 {i}]")
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _xlsx_text(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"[工作表 {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                vals = ["" if v is None else str(v).strip() for v in row]
                if any(vals):
                    parts.append(" | ".join(vals))
    finally:
        wb.close()
    return "\n".join(parts).strip()


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ConvertError("未安装 pypdf，PDF 暂不能转（其余格式不受影响）") from exc
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ConvertError("PDF 已加密，需先解密")
    parts: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(f"[第 {i} 页]\n{t}")
    text = "\n\n".join(parts).strip()
    if len(text) < 20:
        raise ConvertError("PDF 没有文字层（多半是扫描件/图片），需要 OCR，先别导入")
    return text


def convert_one(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PASS_EXT:
        return _decode(path.read_bytes()).strip()
    if ext == ".docx":
        text = _docx_text(path)
    elif ext == ".xlsx":
        text = _xlsx_text(path)
    elif ext == ".pdf":
        text = _pdf_text(path)
    elif ext in LEGACY_EXT:
        raise ConvertError(LEGACY_EXT[ext])
    else:
        raise ConvertError(f"不支持的扩展名 {ext or '(无)'}")
    if not text.strip():
        raise ConvertError("文件是空的，或抽不出文字")
    return text


def init_dirs(src: Path, out: Path) -> list[str]:
    made = []
    for name in DEPT_DIRS:
        d = src / name
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            made.append(str(d))
            marker = d / PLACEHOLDER
            marker.write_text("这个文件夹用来放文件，说明见 corpus_inbox/README.md\n",
                              encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    return made


def run(src: Path, out: Path, force: bool = False, dry_run: bool = False) -> dict:
    files = [p for p in sorted(src.rglob("*"))
             if p.is_file() and p.name != PLACEHOLDER]
    report: dict = {
        "src": str(src), "out": str(out), "dry_run": dry_run,
        "total": len(files), "converted": [], "skipped": [], "failed": [],
    }
    for path in files:
        rel = path.relative_to(src)
        target = out / rel.parent / (path.stem + ".txt")
        item = {"src": str(rel), "dst": str(target.relative_to(out))}
        ext = path.suffix.lower()
        if ext not in PASS_EXT | CONVERT_EXT:
            report["failed"].append({**item, "reason": LEGACY_EXT.get(ext, f"不支持的扩展名 {ext or '(无)'}")})
            continue
        if target.exists() and not force:
            report["skipped"].append({**item, "reason": "已存在同名 txt（--force 可覆盖）"})
            continue
        if dry_run:
            report["converted"].append({**item, "chars": None, "dry": True})
            continue
        try:
            text = convert_one(path)
        except Exception as exc:  # 单篇失败不拖垮整批
            report["failed"].append({**item, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        report["converted"].append({**item, "chars": len(text)})

    if not dry_run:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="原始语料 → 纯文本（保留部门目录）")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--init", action="store_true", help="只建标准目录，然后退出")
    ap.add_argument("--dry-run", action="store_true", help="只列清单，不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的同名 txt")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    a = ap.parse_args()

    src, out = Path(a.src), Path(a.out)
    if a.init or not src.exists():
        made = init_dirs(src, out)
        if a.init:
            print(f"[init] 已就绪：{src}")
            for m in made:
                print("  新建", m)
            return 0

    report = run(src, out, force=a.force, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[convert] 扫描 {report['total']} 个文件 → 转换 {len(report['converted'])}、"
              f"跳过 {len(report['skipped'])}、失败 {len(report['failed'])}")
        for row in report["converted"][:10]:
            print("  OK  ", row["src"], f"({row['chars']} 字)" if row.get("chars") else "")
        for row in report["failed"]:
            print("  FAIL", row["src"], "→", row["reason"])
        if report["failed"]:
            print(f"[convert] 失败明细已写 {REPORT}")
    if report["total"] == 0:
        print("[convert] 没找到文件。把原始文件拖进 corpus_inbox/raw/ 对应部门文件夹，"
              "或先跑 --init 建目录。")
        return 0
    if report["failed"] and not report["converted"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
