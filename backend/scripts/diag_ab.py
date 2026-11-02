"""诊断：在同一份索引上跑「旧算法」与「新算法」，逐段对比中间量。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.filter import single_filter  # noqa: E402
from app.models.caller import CallerContext  # noqa: E402
from app.services import kb_store, knowledge as K  # noqa: E402


def retrieve_old(query, caller, top_k=100):
    """旧实现（M8）逐行搬过来，向量从 _VEC 取以适配新存储。"""
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
    candidates = [K._CHILDREN[i] for i in top_idx]
    # 新存储不再把向量挂在 child dict 上，rerank 的 cos 必须显式喂进去
    cos_map = {K._CHILDREN[i]["child_id"]: float(K._cosine(qvec, K._VEC[i]))
               for i in top_idx}
    reranked = K.rerank(query, candidates, qvec=qvec, cos_map=cos_map)
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
    return visible[:top_k], fo, top_idx


kb_store.load_from_pg()
C = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")

for q in ["直营门店审计留痕 审计留痕 要求是什么",
          "华东大区渠道政策的渠道政策怎么规定",
          "大客户部物料规范 物料规范 怎么要求"]:
    old_hits, _, old_idx = retrieve_old(q, C)
    new_hits, _ = K.retrieve(q, C, 100)
    old_ids = [h["doc_id"] for h in old_hits]
    new_ids = [h["doc_id"] for h in new_hits]
    print(f"\nQ: {q}")
    print(f"  old top3: {[h['title'][:20] for h in old_hits[:3]]}")
    print(f"  new top3: {[h['title'][:20] for h in new_hits[:3]]}")
    print(f"  same order: {old_ids == new_ids} | overlap={len(set(old_ids) & set(new_ids))}"
          f"/{len(old_ids)} old, {len(new_ids)} new")
    if old_ids != new_ids:
        for i, (a, b) in enumerate(zip(old_ids, new_ids)):
            if a != b:
                print(f"  first diff at {i}: old={a} new={b}")
                break
