"""真实 bge-m3 向量化压测（M11）。

M9/M10 的所有延迟数字都是「不含 embedding」的——离线伪向量一条只要几十微秒，
把它算进检索链路等于没算。接上真模型后 query 编码才是主成本项，这个脚本专门量它。

四段测量：
  ① 全量导入：语料 → 分块 → 编码 → 建索引，拆出 embed / 索引 / 缓存各自的占比
  ② 热重建：同一进程清空知识库后重导，验证 embedding 缓存把 embed 成本吃掉了多少
  ③ 检索延迟：冷 query（要编码）vs 热 query（命中缓存）分开报 p50/p95
  ④ 召回：真实语义向量 vs M9 的伪向量基线

用法（必须开本地模型，否则内核直接退化为伪向量，量出来的数没意义）：
  cd backend
  USE_LOCAL_EMBEDDING=1 EMBEDDING_MODEL=D:/models/bge-m3 \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=D:/models/.hfhome \
  EMBED_CACHE=1 PG_PORT=1 SEED_DEMO_CORPUS=0 \
  ./.venv/Scripts/python.exe scripts/bench_embed.py --docs 220 \
      --golden D:/pilot_corpus/golden_v2.jsonl --out reports/bench_embed.json
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings          # noqa: E402
from app.models.caller import CallerContext   # noqa: E402


def pct(xs: list, p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def load_corpus(dirs: list, limit: int) -> list:
    """从目录读原始文档（.txt/.md），保持确定性顺序。"""
    docs = []
    for d in dirs:
        p = Path(d)
        if not p.exists():
            continue
        for f in sorted(p.rglob("*")):
            if f.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            if not text:
                continue
            title = text.splitlines()[0].lstrip("# ").strip() or f.stem
            docs.append({"title": title[:80], "content": text, "filename": f.name})
            if len(docs) >= limit:
                return docs
    return docs


def ingest(docs: list, caller: CallerContext, scope: str = "department") -> dict:
    from app.services import kb_store

    t = time.perf_counter()
    res = kb_store.register_many(docs, caller, scope=scope)
    return {"added": res.get("added", 0), "duplicated": res.get("duplicated", 0),
            "elapsed_s": time.perf_counter() - t}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="+", default=["D:/pilot_corpus/docs"])
    ap.add_argument("--docs", type=int, default=220)
    ap.add_argument("--golden", default="")
    ap.add_argument("--out", default="reports/bench_embed.json")
    ap.add_argument("--queries", type=int, default=12)
    args = ap.parse_args()

    from app.services import embed_cache, knowledge

    model = knowledge._get_st_model()
    if model is None:
        print("!! 本地模型未加载：脚本会量到伪向量，数字没有意义。"
              "请设 USE_LOCAL_EMBEDDING=1 与 EMBEDDING_MODEL=<本地路径>", file=sys.stderr)
        return 2
    print(f"model ready: dim={model.get_embedding_dimension()} | cache={embed_cache.stats()['enabled']}")

    docs = load_corpus(args.dirs, args.docs)
    if not docs:
        print(f"!! {args.dirs} 下没有可用的 .txt/.md", file=sys.stderr)
        return 2
    print(f"corpus: {len(docs)} docs")

    caller = CallerContext(userId="u_bench", deptId="d_bench", role="dept_admin",
                           tenantId="t_bench_m11")
    model.encode(["预热一次，避开线程池初始化"], normalize_embeddings=True)

    # ① 冷启动全量导入
    embed_cache.reset_stats()
    cold = ingest(docs, caller)
    cs = knowledge.corpus_size()
    cold.update({"children": cs["children"], "documents": cs["documents"],
                 "cache": embed_cache.stats()})
    print(f"[1] 全量导入 {cold['documents']} 篇 / {cold['children']} 块 "
          f"{cold['elapsed_s']:.1f}s → {cold['documents']/max(cold['elapsed_s'],1e-9):.1f} 篇/秒")

    # ② 热路径：把全部块文本重新编码一遍 —— 等价于「服务重启后从 PG 载回」，
    #    这一遍的耗时全看 embedding 缓存命中率。
    chunk_texts = [c["text"] for c in knowledge._CHILDREN]
    embed_cache.reset_stats()
    t = time.perf_counter()
    knowledge._embed_many(chunk_texts)
    warm = {"elapsed_s": time.perf_counter() - t, "children": len(chunk_texts),
            "cache": embed_cache.stats()}
    print(f"[2] 重启载回重编码 {warm['children']} 块 {warm['elapsed_s']:.2f}s "
          f"| 缓存命中率 {warm['cache']['hit_rate']:.2%}")

    # ③ 检索延迟：冷 query / 热 query 分开
    import random
    rnd = random.Random(11)
    pool = [d["title"] for d in docs] or ["制度"]
    cold_lat, hot_lat = [], []
    for i in range(args.queries):
        q = f"{rnd.choice(pool)} 具体怎么规定的{i}"       # 每次都新，必然 miss
        t = time.perf_counter()
        knowledge.retrieve(q, caller, top_k=6)
        cold_lat.append((time.perf_counter() - t) * 1000)
    q0 = "差旅住宿标准与报销时限是怎么规定的"
    knowledge.retrieve(q0, caller, top_k=6)              # 预热一次写入缓存
    for _ in range(args.queries):
        t = time.perf_counter()
        knowledge.retrieve(q0, caller, top_k=6)          # 同一 query 反复
        hot_lat.append((time.perf_counter() - t) * 1000)
    print(f"[3] 检索 冷 query p50 {statistics.median(cold_lat):.0f}ms / "
          f"p95 {pct(cold_lat, .95):.0f}ms  ← 含现场编码")
    print(f"    检索 热 query p50 {statistics.median(hot_lat):.1f}ms / "
          f"p95 {pct(hot_lat, .95):.1f}ms  ← 命中缓存")

    # ④ 召回
    recall = {}
    if args.golden and Path(args.golden).exists():
        from app.services import eval as E
        g = E.load_golden_file(args.golden) or []
        # compute_recall_at_k 走 viewer + expected_doc_id 的直查路径；
        # 外部 golden（如 pilot_corpus/golden_v2.jsonl）是「标题反查」格式，
        # 字段对不上就不硬算——错口径的召回比没有召回更误导。
        usable = [r for r in g if r.get("viewer") and r.get("expected_doc_id")]
        if usable:
            for k in (5, 10, 100):
                recall[f"recall@{k}"] = round(E.compute_recall_at_k(usable, k), 4)
            print(f"[4] 召回 {recall} (golden={len(usable)}/{len(g)})")
        elif g:
            print(f"[4] 跳过召回：{len(g)} 条 golden 全是标题反查格式"
                  f"（无 viewer/expected_doc_id），口径不同不能直接算")

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": settings.EMBEDDING_MODEL,
        "embed_dim": model.get_embedding_dimension(),
        "batch_size": settings.EMBED_BATCH_SIZE,
        "vector_backend": settings.VECTOR_BACKEND,
        "corpus": {"docs": len(docs), "children": cs["children"]},
        "cold_import": cold,
        "warm_rebuild": warm,
        "latency_ms": {
            "cold_query": {"p50": round(statistics.median(cold_lat), 1),
                           "p95": round(pct(cold_lat, .95), 1)},
            "hot_query": {"p50": round(statistics.median(hot_lat), 1),
                          "p95": round(pct(hot_lat, .95), 1)},
        },
        "recall": recall,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
