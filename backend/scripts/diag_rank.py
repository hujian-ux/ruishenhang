"""诊断：逐条 golden 的召回排名，用于对比两份实现的实际差异。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.caller import CallerContext  # noqa: E402
from app.services import kb_store, knowledge  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "D:/rank_dump.json"
GOLDEN = sys.argv[2] if len(sys.argv) > 2 else "D:/pilot_corpus/golden.jsonl"

kb_store.load_from_pg()
C = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")

rows = []
with open(GOLDEN, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        g = json.loads(line)
        q = g["query"]
        title = g.get("expected_title") or ""
        want = knowledge.find_by_title(title)
        hits, _ = knowledge.retrieve(q, C, 100)
        ids = [h["doc_id"] for h in hits]
        rank = ids.index(want) + 1 if want and want in ids else None
        rows.append({"query": q[:24], "title": title[:26], "want": want,
                     "found": rank is not None, "rank": rank,
                     "top1": hits[0]["title"][:26] if hits else "",
                     "hits": len(hits)})

hit = sum(1 for r in rows if r["found"])
top10 = sum(1 for r in rows if r["rank"] and r["rank"] <= 10)
print(f"golden={len(rows)} hit@100={hit} ({hit/len(rows):.4f}) hit@10={top10}")
for r in rows[:12]:
    print(f"  rank={r['rank']} top1={r['top1']:<28} want={r['title']}")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print("dumped", OUT)
