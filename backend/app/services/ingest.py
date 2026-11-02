"""批量语料导入（M8 试点第一步：把 200–500 篇真实文档灌进来）。

现状痛点：M7 只支持 UI 单篇上传，500 篇手点 500 次；而且单篇 add_document 每次都
重建全量索引，500 篇 = 分钟级等待。本模块把「扫描 → 解析 → 去重 → 批量入库 →
一次重建索引 → 出报告」串成一条命令：

    python scripts/ingest_corpus.py --dir D:/corpus --scope department --dept d_sales

设计取舍：
  - **去重靠内容指纹**（kb_store.content_hash，归一化空白后 sha256）：
    同一篇被复制成 (1).md、(2).md 只入一次，重跑整批导入全部判为 duplicated。
  - **失败不中断**：单篇解码失败/空文件记入 failed[]，其余照常入库，
    报告里逐条给原因——试点最怕"导了一半不知道哪些没进去"。
  - **格式只做文本类**：txt/md/csv/json/log/yml/yaml/sql/html。PDF/Word 先用
    转换工具落地成文本再导入（不在这里塞重依赖）。
  - **入索引截断**：超长文档全文落库/落对象存储，但只截 INGEST_INDEX_CHAR_CAP
    入索引，避免一篇手册把检索质量带偏。
"""
import html
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext
from app.services import kb_store

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yml", ".yaml", ".sql"}
HTML_EXT = {".html", ".htm"}
SUPPORTED_EXT = TEXT_EXT | HTML_EXT


class IngestError(Exception):
    """单篇解析失败（记入报告，不中断整批）。"""


# ─────────────────────────────────────────────────────────────────────────────
# 解析
# ─────────────────────────────────────────────────────────────────────────────
def _html_to_text(raw: str) -> str:
    """HTML → 纯文本：去 script/style，去标签，反转义实体，压空白。"""
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|tr|h[1-6])>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v\u00a0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IngestError("无法解码（非 UTF-8 / GBK 文本）")


def _title_of(path: Path, content: str) -> str:
    """标题取 md 首个一级标题，否则文件名（去掉 (1)/副本 这类后缀）。"""
    if path.suffix.lower() in (".md", ".markdown"):
        for line in content.splitlines()[:8]:
            s = line.strip()
            if s.startswith("# "):
                return s[2:].strip()[:80]
    stem = path.stem
    stem = re.sub(r"\s*[\(\（]\d+[\)\）]$", "", stem)
    stem = re.sub(r"\s*[-_ ]?副本$", "", stem)
    return stem.strip()[:80] or path.name


def extract(path: Path) -> Dict[str, Any]:
    """解析单篇 → {title, content, chars}；失败抛 IngestError。"""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise IngestError(f"不支持的扩展名 {ext or '(无)'}")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise IngestError(f"读取失败：{e}")
    if not raw:
        raise IngestError("空文件")
    text = _decode(raw)
    if ext in HTML_EXT:
        text = _html_to_text(text)
    text = text.strip()
    if len(text) < 20:
        raise IngestError(f"正文过短（{len(text)} 字），疑为非文本或空壳")
    return {"title": _title_of(path, text), "content": text, "chars": len(text)}


def scan(root: Path, recursive: bool = True,
         exts: Optional[set] = None, max_files: Optional[int] = None) -> List[Path]:
    """扫描目录下的可导入文件（跳过隐藏目录 / node_modules / .git）。"""
    exts = exts or SUPPORTED_EXT
    pattern = "**/*" if recursive else "*"
    out: List[Path] = []
    skip_parts = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
    for p in sorted(root.glob(pattern)):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if any(part in skip_parts or part.startswith(".") for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p)
    return out[: max_files] if max_files else out


# ─────────────────────────────────────────────────────────────────────────────
# 导入
# ─────────────────────────────────────────────────────────────────────────────
def import_corpus(root: str | Path, caller: CallerContext,
                  scope: str = "department",
                  dept_id: Optional[str] = None,
                  recursive: bool = True,
                  max_files: Optional[int] = None,
                  dry_run: bool = False,
                  progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]:
    """目录批量导入，返回可归档的导入报告（JSON 友好）。"""
    t0 = time.time()
    root_path = Path(root)
    if not root_path.is_dir():
        raise IngestError(f"目录不存在：{root_path}")

    limit = max_files or settings.INGEST_MAX_FILES
    files = scan(root_path, recursive=recursive, max_files=limit)

    rows: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    for i, p in enumerate(files, start=1):
        try:
            item = extract(p)
            rows.append({**item, "filename": p.name,
                         "path": str(p.relative_to(root_path)).replace("\\", "/")})
        except IngestError as e:
            failed.append({"path": str(p.relative_to(root_path)).replace("\\", "/"),
                           "reason": str(e)})
        if progress:
            progress(i, len(files), p.name)

    if dry_run:
        result = {"added": 0, "duplicated": 0, "total": len(rows), "chars_added": 0,
                  "docs": [{"title": r["title"], "path": r["path"], "chars": r["chars"],
                            "duplicated": False, "doc_id": None} for r in rows]}
    else:
        result = kb_store.register_many(rows, caller, scope=scope, dept_id=dept_id,
                                        source="bulk_import")
        by_title = {r["title"]: r for r in rows}
        for d in result["docs"]:
            src = by_title.get(d.get("title") or "")
            d["path"] = src["path"] if src else ""

    elapsed = round((time.time() - t0) * 1000, 1)
    report = {
        "ok": not failed or result["added"] > 0,
        "dry_run": dry_run,
        "root": str(root_path),
        "scope": scope,
        "dept_id": dept_id or caller.deptId,
        "tenant_id": caller.tenantId,
        "owner_id": caller.userId,
        "scanned": len(files),
        "parsed": len(rows),
        "added": result["added"],
        "duplicated": result["duplicated"],
        "failed_count": len(failed),
        "failed": failed,
        "chars_added": result["chars_added"],
        "elapsed_ms": elapsed,
        "files_per_sec": round(len(files) / (elapsed / 1000), 1) if elapsed else 0,
        "index_char_cap": settings.INGEST_INDEX_CHAR_CAP,
        "truncated": sum(1 for r in rows if r["chars"] > settings.INGEST_INDEX_CHAR_CAP),
        "sample": [{"title": d.get("title"), "doc_id": d.get("doc_id"),
                    "duplicated": d.get("duplicated")} for d in result["docs"][:5]],
        "docs": result["docs"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return report


def write_report(report: Dict[str, Any], out_dir: str | Path,
                 name: str = "ingest_report") -> Dict[str, str]:
    """落盘 JSON + 人读 Markdown 摘要，返回两个路径。"""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"{name}.json"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 批量导入报告 · {report['root']}",
        "",
        f"生成时间：{report['generated_at']}　范围：{report['scope']} / {report['dept_id']}　租户：{report['tenant_id']}",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 扫描文件 | {report['scanned']} |",
        f"| 解析成功 | {report['parsed']} |",
        f"| **新增入库** | **{report['added']}** |",
        f"| 重复跳过（内容指纹相同） | {report['duplicated']} |",
        f"| 失败 | {report['failed_count']} |",
        f"| 新增字符 | {report['chars_added']:,} |",
        f"| 耗时 | {report['elapsed_ms']} ms（{report['files_per_sec']} 篇/秒） |",
        f"| 超长截断入索引 | {report['truncated']} 篇（原文完整保留） |",
        "",
    ]
    if report["failed"]:
        lines += ["## 失败清单（需人工处理）", "", "| 文件 | 原因 |", "| --- | --- |"]
        lines += [f"| {f['path']} | {f['reason']} |" for f in report["failed"]]
        lines.append("")
    mp = out / f"{name}.md"
    mp.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(jp), "markdown": str(mp)}
