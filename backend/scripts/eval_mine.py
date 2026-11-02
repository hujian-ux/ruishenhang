"""golden 治理：从跑批结果里挖「坏例」，产出可人工确认的候选条目。

为什么需要：
  golden 是评测的地基。试点跑两周后，真实 query 分布跟第一天的 44 条差别很大，
  但没人有精力凭记忆补——于是评测慢慢变成「只测那几条好测的」。
  这个脚本把低召回 query 连同**实际召回的前几名**列出来，人只需要判断
  「是 golden 标错了」还是「检索确实不行」，然后把确认的条目追加进 golden。

用法：
  SEED_DEMO_CORPUS=0 ./.venv/Scripts/python.exe scripts/eval_mine.py \
      --golden D:/pilot_corpus/golden.jsonl --out D:/pilot_corpus/candidates.jsonl --top 3

输出每行一个候选（可直接追加到 golden，但建议先人工过一遍）：
  {"query": "...", "expected_title": "...", "note": "原 golden miss，实际 top1：xxx"}
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.caller import CallerContext  # noqa: E402
from app.services import eval as ev  # noqa: E402
from app.services import kb_store, knowledge  # noqa: E402


def family(title: str) -> str:
    """去掉尾部编号，取文档族名：「华东大区渠道政策（2026-041）」→「华东大区渠道政策」。

    同一族往往有几十篇（不同年份/版本/区域），query 问「渠道政策怎么执行」时
    召回同族另一篇其实是**能答**的。golden 只标一个 doc_id 会把这类算成漏检，
    让指标系统性偏低——先把它们挑出来，别让人白改检索。
    """
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", (title or "").strip())
    return t.strip() or (title or "")


def main() -> None:
    ap = argparse.ArgumentParser(description="从评测结果挖坏例，产出 golden 候选")
    ap.add_argument("--golden", required=True, help="当前 golden 文件（jsonl/json）")
    ap.add_argument("--out", default="reports/golden_candidates.jsonl")
    ap.add_argument("--top", type=int, default=3, help="列出实际召回的前 N 篇做参照")
    ap.add_argument("--k", type=int, default=10, help="超过该排名即视为坏例")
    ap.add_argument("--load-pg", action="store_true", default=True)
    args = ap.parse_args()

    if args.load_pg:
        n = kb_store.load_from_pg()
        if n:
            print(f"已从 PG 载回 {n} 篇语料")

    raw = ev.load_golden_file(args.golden)
    golden, bad = ev.normalize_golden(raw)
    print(f"golden 可用 {len(golden)} 条，丢弃 {len(bad)} 条")
    for b in bad[:5]:
        print(f"  丢弃 line {b['line']}: {b['reason']}")

    misses = []
    for g in golden:
        caller = CallerContext(**g["viewer"])
        hits, _ = knowledge.retrieve(g["query"], caller, max(args.k, args.top))
        ids = [h["doc_id"] for h in hits]
        target = g["expected_doc_id"]
        rank = ids.index(target) + 1 if target in ids else 0
        if rank == 0 or rank > args.k:
            exp_title = next((p["title"] for p in knowledge.get_parents()
                              if p["doc_id"] == target), "")
            top = [{"title": h["title"], "score": h["score"]} for h in hits[: args.top]]
            # 同族等价判断：实际 top 里有和期望标题同族的文档 → 大概率能答，属 golden 过严
            same = [t for t in top if family(t["title"]) == family(exp_title)]
            misses.append({
                "query": g["query"],
                "expected_title": exp_title,
                "expected_doc_id": target,
                "rank": rank or None,
                "equivalent": bool(same),
                "equivalent_title": same[0]["title"] if same else None,
                "actual_top": top,
                "scope": g.get("scope"),
            })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for m in misses:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    print(f"\n坏例 {len(misses)}/{len(golden)}（rank>{args.k} 或未命中）")
    equiv = [m for m in misses if m["equivalent"]]
    real = [m for m in misses if not m["equivalent"]]
    for m in misses[:12]:
        top = m["actual_top"][0]["title"][:26] if m["actual_top"] else "-"
        tag = "同族等价" if m["equivalent"] else "真漏检"
        print(f"  [{tag}] rank={m['rank']} query={m['query'][:22]:<24} 实际 top1={top}")
    print(f"\n其中同族等价 {len(equiv)} 条（golden 过严，建议加 accept_titles），"
          f"真漏检 {len(real)} 条（值得改检索）")
    print(f"候选已写入 {out}（每行含实际召回参照，人工确认后再并入 golden）")


if __name__ == "__main__":
    main()
