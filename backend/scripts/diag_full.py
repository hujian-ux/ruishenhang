"""诊断：旧算法（retrieve_old）跑完整 golden，判断问题在算法还是存储。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.filter import single_filter  # noqa: E402
from app.services import eval as ev  # noqa: E402
from app.services import kb_store, knowledge as K  # noqa: E402


def retrieve_old(query, caller, top_k=100):
    qtoks = K._tokenize(query)
    qvec = K.embed(query)
    n = K._ROWS
    vec_rank = sorted(range(n), key=lambda i: -K._cosine(qvec, K._VEC[i]))
    bm25_scores = [K._bm25(qtoks, K._CHILDREN[i]["_toks"]) for i in range(n)]
    bm25_rank = sorted(range(n), key=lambda i: bm25_scores[i], reverse=True)
    rrf = [0.0] * n
    for rank, i in enumerate(vec_rank):
        rrf[i] += 1.0 / (K.RRF_K + rank + 1)
    for rank, i in enumerate(bm25_rank):
        rrf[i] += 1.0 / (K.RRF_K + rank + 1)
    top_idx = sorted(range(n), key=lambda i: rrf[i], reverse=True)[:100]
    candidates = []
    for i in top_idx:
        c = dict(K._CHILDREN[i])
        c["_vec"] = K._VEC[i]
        candidates.append(c)
    reranked = K.rerank(query, candidates, qvec=qvec)
    parent_best = {}
    for c, sc in reranked:
        pid = c["parent_id"]
        rep = parent_best.get(pid)
        if rep is None or sc > rep["score"]:
            parent_best[pid] = {
                "doc_id": pid, "title": c["title"], "snippet": c["text"],
                "scope": c["scope"], "kb_tier": c["scope"], "score": round(sc, 4),
                "source": c["source"], "child_id": c["child_id"],
                "owner": c["owner"], "dept": c["dept"], "tenant_id": c["tenant_id"],
            }
    agg = sorted(parent_best.values(), key=lambda x: x["score"], reverse=True)
    visible, fo = single_filter(agg, caller)
    return visible[:top_k], fo


kb_store.load_from_pg()
raw = ev.load_golden_file("D:/pilot_corpus/golden.jsonl")
golden, dropped = ev.normalize_golden(raw)
print(f"golden={len(golden)} dropped={len(dropped)} children={K._ROWS}")

for name, fn in (("old", retrieve_old), ("new", K.retrieve)):
    hit100 = hit10 = 0
    for g in golden:
        caller = ev.CallerContext(**g["viewer"])
        hits, _ = fn(g["query"], caller, 100)
        ids = [h["doc_id"] for h in hits]
        t = g["expected_doc_id"]
        if t and t in ids:
            rank = ids.index(t) + 1
            hit100 += 1
            hit10 += 1 if rank <= 10 else 0
    print(f"{name}: hit@100={hit100}/{len(golden)}={hit100/len(golden):.4f} "
          f"hit@10={hit10}")
