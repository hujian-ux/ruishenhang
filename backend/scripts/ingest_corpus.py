"""批量导入知识库语料（M8 试点第一步）。

用法（Git Bash / PowerShell 均可，须用 venv 的 python）：

    cd D:/enterprise-ai-platform/backend
    ./.venv/Scripts/python.exe scripts/ingest_corpus.py --dir D:/corpus/sales --dept d_sales
    # 先干跑看会导多少篇、哪些失败（不写库）
    ./.venv/Scripts/python.exe scripts/ingest_corpus.py --dir D:/corpus/sales --dry-run
    # 集团级语料（仅平台管理员语义）
    ./.venv/Scripts/python.exe scripts/ingest_corpus.py --dir D:/corpus/handbook --scope global --role platform_admin

行为：
  1. 先尝试从 PG 载回已有语料（决定去重基线；PG 不可达则纯内存，重跑仍会判重）
  2. 扫描 → 解析 → 内容指纹去重 → 批量入库（索引只重建一次）
  3. 报告落 backend/reports/ingest_report.{json,md}，控制台打印摘要
退出码：0 = 有新增或全部为重复（无失败）；2 = 存在失败文件且无任何新增。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.caller import CallerContext  # noqa: E402
from app.services import ingest, kb_store, pg_store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="企业 AI 中台 · 批量语料导入")
    ap.add_argument("--dir", required=True, help="语料根目录（递归扫描）")
    ap.add_argument("--scope", default="department", choices=["personal", "department", "global"])
    ap.add_argument("--dept", default="", help="归属部门（缺省 = 调用者部门）")
    ap.add_argument("--user", default="u_admin_01", help="归属人 userId")
    ap.add_argument("--role", default="dept_admin",
                    choices=["employee", "dept_admin", "platform_admin"])
    ap.add_argument("--tenant", default="t_demo")
    ap.add_argument("--no-recursive", action="store_true", help="只扫一层")
    ap.add_argument("--max-files", type=int, default=0, help="最多导入多少篇（0=不限制）")
    ap.add_argument("--dry-run", action="store_true", help="只解析不写库，用于先看清单")
    ap.add_argument("--report-dir", default=str(Path(__file__).resolve().parent.parent / "reports"))
    ap.add_argument("--json", action="store_true", help="只输出 JSON（给脚本消费）")
    args = ap.parse_args()

    caller = CallerContext(userId=args.user, deptId=args.dept or "d_sales",
                           role=args.role, tenantId=args.tenant)
    restored = kb_store.load_from_pg()

    def progress(i: int, n: int, name: str) -> None:
        if not args.json and (i % 25 == 0 or i == n):
            print(f"  解析 {i}/{n} … {name[:40]}", flush=True)

    try:
        report = ingest.import_corpus(
            args.dir, caller, scope=args.scope, dept_id=args.dept or None,
            recursive=not args.no_recursive,
            max_files=args.max_files or None, dry_run=args.dry_run, progress=progress)
    except ingest.IngestError as e:
        print(f"导入失败：{e}", file=sys.stderr)
        return 2

    report["pg_available"] = pg_store.available()
    report["restored_from_pg"] = restored
    paths = ingest.write_report(report, args.report_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        tag = "干跑（未写库）" if report["dry_run"] else "已入库"
        print("─" * 56)
        print(f"{tag} · {report['root']}")
        print(f"  扫描 {report['scanned']} 篇 → 新增 {report['added']} · 重复 {report['duplicated']}"
              f" · 失败 {report['failed_count']} · 新增 {report['chars_added']:,} 字")
        print(f"  耗时 {report['elapsed_ms']} ms（{report['files_per_sec']} 篇/秒）"
              f" · PG {'已连接' if report['pg_available'] else '不可达（纯内存）'}")
        if report["failed"]:
            print("  失败明细：")
            for f in report["failed"][:10]:
                print(f"    - {f['path']}：{f['reason']}")
        print(f"  报告：{paths['json']}")
        print(f"        {paths['markdown']}")

    if report["failed_count"] and report["added"] == 0 and not report["dry_run"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
