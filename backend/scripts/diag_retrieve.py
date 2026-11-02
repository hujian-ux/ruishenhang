"""诊断：导出单条 query 的检索中间状态，用于新旧实现逐段对比。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.caller import CallerContext  # noqa: E402
from app.services import kb_store, knowledge  # noqa: E402

QUERY = sys.argv[1] if len(sys.argv) > 1 else "华东大区渠道政策的渠道政策怎么规定"
OUT = sys.argv[2] if len(sys.argv) > 2 else "D:/tmp_dump.json"

kb_store.load_from_pg()
C = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")

knowledge.retrieve(QUERY, C, 10)  # 预热（确保索引已建）

qtoks = knowledge._tokenize(QUERY)
qvec = knowledge.embed(QUERY)

# 两种口径的向量排序：纯 python 稳定排序 vs numpy
try:
    import numpy as np
    scores = np.asarray([knowledge._cosine(qvec, c.get("_vec") or knowledge._VEC[i])
                         for i, c in enumerate(knowledge._CHILDREN)], dtype=np.float32) \
        if knowledge._VEC is not None else None
except Exception:
    scores = None

py_sorted = sorted(range(knowledge._ROWS),
                   key=lambda i: -knowledge._cosine(qvec, knowledge._VEC[i]))[:20] \
    if knowledge._VEC is not None else []

bm = [(i, knowledge._bm25(qtoks, knowledge._CHILDREN[i]["_toks"]))
      for i in range(knowledge._ROWS) if knowledge._ALIVE[i]]
bm.sort(key=lambda x: -x[1])

hits, fo = knowledge.retrieve(QUERY, C, 100)

dump = {
    "rows": knowledge._ROWS,
    "children": knowledge._N,
    "py_vec_top20": py_sorted,
    "bm_top20": [i for i, _ in bm[:20]],
    "bm_scores_top10": [(i, round(s, 6)) for i, s in bm[:10]],
    "py_vec_titles": [knowledge._CHILDREN[i]["title"][:24] for i in py_sorted[:10]],
    "bm_titles": [knowledge._CHILDREN[i]["title"][:24] for i, _ in bm[:10]],
    "hits": [(h["title"][:24], h["score"]) for h in hits[:10]],
    "hits_len": len(hits),
    "filtered": fo,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(dump, f, ensure_ascii=False, indent=2)
print("dumped", OUT)
for k, v in dump.items():
    print(k, "=", v)
sys.stdout.flush()
