"""跑一次 RAG 基线评测（M8 试点：真语料 + 真 golden，出可归档报告）。

用法：

    cd D:/enterprise-ai-platform/backend
    # 用内置 golden（18 篇演示语料），并为报告落盘
    ./.venv/Scripts/python.exe scripts/eval_baseline.py
    # 用试点自己写的 golden（推荐：只写 query + expected_title，doc_id 由系统反查）
    ./.venv/Scripts/python.exe scripts/eval_baseline.py --golden D:/corpus/golden.jsonl --gate
    # 只跑前 50 条快速自检
    ./.venv/Scripts/python.exe scripts/eval_baseline.py --limit 50

golden 格式（.jsonl，每行一条，# 开头为注释）：

    {"query": "满 199 减多少", "expected_title": "五一营销活动方案 v3"}
    {"query": "报销多久截止", "expected_title": "财务报销流程", "scope": "global"}
    {"query": "张三的客户跟进到哪了", "expected_doc_id": "doc_6006"}

可选字段：expected_doc_id / scope / viewer{userId,deptId,role,tenantId} / tenant_id。
退出码：0 = 门禁通过；1 = 门禁未通过；2 = 执行失败（golden 为空 / 文件不存在）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services import eval as eval_svc, kb_store, pg_store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="企业 AI 中台 · RAG 基线评测跑批")
    ap.add_argument("--golden", default="", help="外部 golden 路径（.jsonl / .json）；缺省用内置集")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    ap.add_argument("--ks", default="5,10,100", help="要算的 K，逗号分隔")
    ap.add_argument("--out-dir", default="", help="报告输出目录（默认 backend/reports）")
    ap.add_argument("--gate", action="store_true", help="不达标时以非零码退出（CI / 每日巡检用）")
    ap.add_argument("--no-filter", action="store_true", help="跳过过滤正确率")
    args = ap.parse_args()

    restored = kb_store.load_from_pg()   # 语料先在位，评测才有意义
    ks = tuple(int(x) for x in args.ks.split(",") if x.strip())
    try:
        report = eval_svc.evaluate(golden_path=args.golden or None, ks=ks,
                                   with_filter=not args.no_filter)
    except (ValueError, FileNotFoundError) as e:
        print(f"评测失败：{e}", file=sys.stderr)
        return 2

    if args.limit and args.limit < report["golden_size"]:
        golden = (eval_svc.load_golden_file(args.golden) if args.golden
                  else (eval_svc.load_golden() or eval_svc.build_golden_entries()))
        golden, dropped = eval_svc.normalize_golden(golden)
        report = eval_svc.evaluate(golden=golden[: args.limit], ks=ks,
                                   with_filter=not args.no_filter)
        report["dropped"] = dropped

    report["pg_available"] = pg_store.available()
    report["restored_from_pg"] = restored
    report["index_char_cap"] = settings.INGEST_INDEX_CHAR_CAP
    paths = eval_svc.write_report(report, args.out_dir or None)

    m = report["metrics"]
    print("─" * 56)
    print(f"基线评测 · golden {report['golden_size']} 条 · 语料 {report['corpus']['documents']} 篇"
          f" / {report['corpus']['children']} 子块 · {report['duration_ms']} ms")
    for k in [k for k in m if k.startswith(("recall", "mrr", "ndcg"))]:
        print(f"  {k:<12} {m[k]}")
    print(f"  过滤正确率     {report['filter_accuracy']}")
    for sc, s in (report["by_scope"] or {}).items():
        print(f"  [{sc}] {s['queries']} 条 · Recall@10 {s['recall@10']} · MRR {s['mrr']}")
    print(f"门禁：{'PASS ✅' if report['gate']['passed'] else 'FAIL ❌'} — {report['gate']['reason']}")
    print(f"报告：{paths['json']}")
    print(f"      {paths['markdown']}")
    if args.gate and not report["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
