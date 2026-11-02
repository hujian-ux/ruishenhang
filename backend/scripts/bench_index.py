"""索引性能基准（M9）：量化「全量重建 + 线性扫」在规模化下的代价。

回答三个问题：
  1. 一次性导入 N 篇要多久？（=_reindex 全量重建的 O(N) 代价）
  2. 已有 N 篇时再增量 100 篇要多久？（增量写入的真实成本）
  3. 检索 p50/p95 随 N 怎么涨？（每次 query 是否在扫全量）

用法：
  SEED_DEMO_CORPUS=0 ./.venv/Scripts/python.exe scripts/bench_index.py --dirs D:/pilot_corpus/docs D:/pilot_corpus_big/docs
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.caller import CallerContext  # noqa: E402
from app.services import knowledge  # noqa: E402

CALLER = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")

QUERIES = [
    "到店礼满减怎么设置", "客户分级 A 级配专属顾问", "合同十万以下谁审批",
    "私域社群每天推送几条", "设计稿交付要哪些文件", "年假按司龄怎么算",
    "报销发票什么时候截止", "季度目标怎么拆到周", "视觉素材商用授权",
    "新人入职首日流程", "市场费用预算按渠道拆分", "信息安全能否外发客户数据",
]


def load_docs(folder: str) -> list[str]:
    """按文件名顺序读入正文，保证多次跑同一份语料可复现。"""
    files = sorted(Path(folder).glob("*"))
    return [f.read_text(encoding="utf-8", errors="ignore") for f in files if f.is_file()]


def import_rows(texts: list[str], scope: str = "department", dept: str = "d_sales") -> tuple[int, float]:
    rows = []
    for i, t in enumerate(texts):
        # 标题取首行，模拟 ingest 的真实行为
        title = (t.splitlines() or ["(无标题)"])[0][:60].strip("# ").strip() or f"doc{i}"
        rows.append({"title": title, "content": t, "scope": scope, "owner": "u_1024",
                     "dept": dept, "tenant_id": "t_demo", "source": "bench"})
    t0 = time.time()
    knowledge.add_documents_bulk(rows)
    return len(rows), time.time() - t0


def probe_latency(rounds: int = 3) -> dict:
    lats = []
    for _ in range(rounds):
        for q in QUERIES:
            t0 = time.perf_counter()
            knowledge.retrieve(q, CALLER, top_k=6)
            lats.append((time.perf_counter() - t0) * 1000)
    lats.sort()
    return {
        "n": len(lats),
        "p50_ms": round(statistics.median(lats), 2),
        "p95_ms": round(lats[int(len(lats) * 0.95) - 1], 2),
        "max_ms": round(lats[-1], 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="+", required=True, help="按顺序累加的语料目录")
    ap.add_argument("--inc", type=int, default=100, help="增量批次大小")
    ap.add_argument("--repeat", type=int, default=50, help="增量重复批次数（每批 --inc 篇）")
    ap.add_argument("--out", default="reports/index_bench.json")
    args = ap.parse_args()

    random.seed(7)
    report = {"stages": []}

    t0 = time.time()
    total = 0
    for folder in args.dirs:
        texts = load_docs(folder)
        n, dt = import_rows(texts)
        total += n
        size = knowledge.corpus_size()
        lat = probe_latency()
        stage = {"stage": f"bulk {n}", "documents": total, "children": size["children"],
                 "import_s": round(dt, 2), "docs_per_s": round(n / dt, 1) if dt else None,
                 "index_build_s": round(size.get("build_ms", 0) / 1000, 2), **lat}
        report["stages"].append(stage)
        print(f"[全量] +{n:5d} 篇 → 累计 {total:5d} 篇 / {size['children']:6d} 子块 |"
              f" 导入 {dt:6.2f}s ({stage['docs_per_s'] or 0:6.1f} 篇/s) |"
              f" 检索 p50 {lat['p50_ms']:7.2f}ms p95 {lat['p95_ms']:7.2f}ms")

    # 增量：复用已导入语料的前若干篇改标题重灌，模拟「每天新增一小批」
    pool = load_docs(args.dirs[0])[: args.inc * 2]
    inc_times = []
    for r in range(args.repeat):
        batch = [f"{pool[i % len(pool)]}\n增量标记 {r}-{i}" for i in range(args.inc)]
        _, dt = import_rows(batch)
        inc_times.append(dt)
    report["incremental"] = {
        "batches": args.repeat, "batch_size": args.inc,
        "avg_s": round(statistics.mean(inc_times), 3),
        "p95_s": round(sorted(inc_times)[int(len(inc_times) * 0.95) - 1], 3),
        "total_s": round(sum(inc_times), 2),
        "documents_now": knowledge.corpus_size()["documents"],
    }
    print(f"[增量] {args.repeat} 批 × {args.inc} 篇 = {args.repeat * args.inc} 篇 |"
          f" 均 {report['incremental']['avg_s']:.2f}s/批 p95 {report['incremental']['p95_s']:.2f}s")

    report["final_latency"] = probe_latency()
    report["final_size"] = knowledge.corpus_size()
    report["elapsed_s"] = round(time.time() - t0, 1)
    print(f"[最终] {report['final_size']['documents']} 篇 / {report['final_size']['children']} 子块 |"
          f" 检索 p50 {report['final_latency']['p50_ms']:.2f}ms"
          f" p95 {report['final_latency']['p95_ms']:.2f}ms | 总耗时 {report['elapsed_s']}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告 → {out}")


if __name__ == "__main__":
    main()
