"""多租户规模化压测（M10）：一个索引里放三个部门，验证「隔离不塌、召回不掉、延迟不涨」。

要回答的三个问题：
  1. **隔离**：某部门管理员检索时，结果里会不会混进别的部门 / 别的租户 / 别人的私有文档？
  2. **质量**：三个部门有大量同主题文档（同一份制度各自的版本）时，
     跨部门候选会不会把本部门的正确答案挤出 top-k（即「过滤点吃掉了召回」）？
  3. **成本**：过滤本身要多少钱？2400 篇下检索 p50 是多少？

用法：
    cd D:/enterprise-ai-platform/backend
    ./.venv/Scripts/python.exe scripts/bench_multitenant.py --per-dept 800
    ./.venv/Scripts/python.exe scripts/bench_multitenant.py --per-dept 100 --with-pg   # 连真 PG

默认不连 PG（写进试点库会污染真实语料），只测内存 + 对象存储路径。
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

# 必须在 import app.core.config 之前设置：Settings 是类属性，导入即固化
os.environ.setdefault("SEED_DEMO_CORPUS", "0")
os.environ.setdefault("EMBED_CACHE", "1")
if "--with-pg" not in sys.argv:
    os.environ["PG_PORT"] = "1"     # 不可达端口 → pg_store 静默退化，绝不写试点库

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_pilot_corpus import FAMILIES, SCENES, make_doc  # noqa: E402

from app.models.caller import CallerContext  # noqa: E402
from app.services import kb_store, knowledge  # noqa: E402

TENANT = "t_acme"
OTHER_TENANT = "t_globex"
DEPTS = [("d_sales", "销售体系"), ("d_after", "售后体系"), ("d_fin", "财务体系")]


def build_queries(per_dept: int, n: int = 24) -> list:
    """跨部门同题 query：不带部门/租户信息，三个部门都有同题文档（各自版本）。

    这是最坏情况：三个部门各有一份「华东大区渠道政策」，谁检索都该只看到自己那份。
    """
    qs = []
    for fam, topics in FAMILIES.items():
        for base, _ in topics:
            # 只取该部门语料里真实出现过的场景（per_dept 小时场景不全）
            for scene in SCENES[: max(1, min(3, per_dept // 8))]:
                qs.append({"query": f"{scene}{base}是怎么规定的，需要注意什么",
                           "family": fam, "base": base, "scene": scene})
    random.Random(7).shuffle(qs)
    return qs[:n]


def import_dept(dept: str, per_dept: int, seed: int) -> dict:
    """把一个部门的语料灌进去，返回 {added, seconds, docs: [(title, doc_id)]}。"""
    caller = CallerContext(userId=f"u_{dept}", deptId=dept, role="dept_admin", tenantId=TENANT)
    rnd = random.Random(seed)
    fams = list(FAMILIES)
    rows = []
    for i in range(per_dept):
        fam = fams[i % len(fams)]
        title, content = make_doc(fam, i, rnd, tag=dept)
        rows.append({"title": title, "content": content, "filename": f"{dept}_{i:04d}.md"})
    t0 = time.perf_counter()
    res = kb_store.register_many(rows, caller, scope="department", dept_id=dept)
    sec = time.perf_counter() - t0
    return {"added": res["added"], "duplicated": res["duplicated"], "seconds": round(sec, 3),
            "docs_per_s": round(res["added"] / sec, 1) if sec else None,
            "docs": [(d["title"], d["doc_id"]) for d in res["docs"] if not d["duplicated"]]}


def add_noise(per_dept: int) -> dict:
    """加两类「不该被看见」的样本：别的租户的部门文档 + 别人的私有文档。"""
    rnd = random.Random(999)
    other = CallerContext(userId="u_other", deptId="d_sales", role="dept_admin",
                          tenantId=OTHER_TENANT)
    rows = []
    for i in range(max(20, per_dept // 10)):
        title, content = make_doc("销售渠道", i, rnd, tag="globex")
        rows.append({"title": title, "content": content})
    res_other = kb_store.register_many(rows, other, scope="department", dept_id="d_sales")

    # 兄弟部门员工的私有文档（同部门的人也不该看到别人的 personal）
    peer = CallerContext(userId="u_peer", deptId="d_sales", role="employee", tenantId=TENANT)
    priv = []
    for i in range(5):
        title, content = make_doc("财务报销", i, rnd, tag="private")
        priv.append({"title": title, "content": content})
    res_priv = kb_store.register_many(priv, peer, scope="personal")
    return {"other_tenant": res_other["added"], "peer_private": res_priv["added"]}


def probe(identity: dict, queries: list, expected: dict, top_k: int = 10,
          with_quality: bool = True) -> dict:
    """以某个身份跑完全部 query，统计泄露 / 空结果 / 召回 / 延迟。"""
    caller = CallerContext(**identity)
    leaks, empties, ranks, lat, filtered = 0, 0, [], [], []
    for q in queries:
        t0 = time.perf_counter()
        hits, filtered_out = knowledge.retrieve(q["query"], caller, top_k=top_k)
        lat.append((time.perf_counter() - t0) * 1000)
        filtered.append(filtered_out)
        if not hits:
            empties += 1
            continue
        # 泄露判定：租户不同 / 部门文档跨部门 / 私有文档不是本人
        for h in hits:
            if h.get("tenant_id") != caller.tenantId:
                leaks += 1
            elif h.get("scope") == "department" and h.get("dept") != caller.deptId:
                leaks += 1
            elif h.get("scope") == "personal" and h.get("owner") != caller.userId:
                leaks += 1
        wants = expected.get((identity["deptId"], q["family"], q["base"], q["scene"]))
        if wants:
            titles = [h["title"] for h in hits]
            hit_ranks = [titles.index(w) + 1 for w in wants if w in titles]
            ranks.append(min(hit_ranks) if hit_ranks else 0)
    lat_sorted = sorted(lat)
    p50 = statistics.median(lat_sorted)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * 0.95))]
    ranks = ranks if with_quality else []
    recall = sum(1 for r in ranks if r) / len(ranks) if ranks else None
    mrr = sum(1.0 / r for r in ranks if r) / len(ranks) if ranks else None
    return {
        "who": f'{identity["userId"]}/{identity["role"]}/{identity["deptId"]}',
        "queries": len(queries), "hits_ok": len(queries) - empties, "empty_queries": empties,
        "leaks": leaks, "recall@10": round(recall, 4) if recall is not None else None,
        "mrr@10": round(mrr, 4) if mrr is not None else None,
        "latency_p50_ms": round(p50, 2), "latency_p95_ms": round(p95, 2),
        "filtered_out_avg": round(statistics.mean(filtered), 1) if filtered else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="多租户规模化压测")
    ap.add_argument("--per-dept", type=int, default=800, help="每个部门多少篇")
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--out", default="reports/bench_multitenant.json")
    ap.add_argument("--with-pg", action="store_true", help="连真 PG（会写库，默认不连）")
    args = ap.parse_args()

    t_start = time.perf_counter()
    kb_store.reset()

    # ── 1) 导入三个部门 ──
    expected: dict = {}       # (dept, family, base) → 该部门对应的标题
    per_dept_stat = []
    for di, (dept, label) in enumerate(DEPTS):
        st = import_dept(dept, args.per_dept, args.seed + di)
        per_dept_stat.append({"dept": dept, "label": label, **{k: v for k, v in st.items() if k != "docs"}})
        fams = list(FAMILIES)
        for i, (title, _doc_id) in enumerate(st["docs"]):
            fam = fams[i % len(fams)]
            base = FAMILIES[fam][i % len(FAMILIES[fam])][0]
            scene = SCENES[i % len(SCENES)]
            # 同族等价：同一部门里「同场景同主题」会有多篇（编号不同），任一篇命中即算召回。
            # 这正是 M9 golden 治理的结论——单目标标注会把「同族命中」误判成漏检。
            expected.setdefault((dept, fam, base, scene), []).append(title)
        print(f"[导入] {dept} +{st['added']} 篇 {st['seconds']}s（{st['docs_per_s']} 篇/秒）")

    noise = add_noise(args.per_dept)
    size = knowledge.corpus_size()
    print(f"[语料] {size['documents']} 篇 / {size['children']} 子块 / 范围 {size['by_scope']}")

    # ── 2) 多身份探针 ──
    queries = build_queries(args.per_dept)
    identities = []
    for dept, _ in DEPTS:
        identities.append({"userId": f"u_{dept}", "deptId": dept, "role": "dept_admin", "tenantId": TENANT})
    identities.append({"userId": "u_emp", "deptId": "d_sales", "role": "employee", "tenantId": TENANT})
    identities.append({"userId": "u_boss", "deptId": "d_sales", "role": "platform_admin", "tenantId": TENANT})
    identities.append({"userId": "u_globex", "deptId": "d_sales", "role": "dept_admin", "tenantId": OTHER_TENANT})

    rows = []
    for ident in identities:
        # 质量只在「本租户 + 单部门视角」下评估：平台管理员跨部门看全量、其他租户根本没有
        # 这批文档，两者的 Recall 都不代表检索质量。
        quality = ident["tenantId"] == TENANT and ident["role"] in ("employee", "dept_admin")
        r = probe(ident, queries, expected, with_quality=quality)
        rows.append(r)
        print(f"[探针] {r['who']:<34} 命中 {r['hits_ok']}/{r['queries']} 空结果 {r['empty_queries']} "
              f"泄露 {r['leaks']} Recall@10 {r['recall@10']} p50 {r['latency_p50_ms']}ms")

    # ── 3) 结论 ──
    leaked = sum(r["leaks"] for r in rows)
    empties = sum(r["empty_queries"] for r in rows)
    recalls = [r["recall@10"] for r in rows if r["recall@10"] is not None]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "per_dept": args.per_dept, "depts": [d for d, _ in DEPTS],
        "pg_available": not (os.environ.get("PG_PORT") == "1"),
        "corpus": size, "noise": noise,
        "import": per_dept_stat,
        "import_total_s": round(sum(p["seconds"] for p in per_dept_stat), 3),
        "identities": rows,
        "summary": {
            "leaks_total": leaked,
            "isolated": leaked == 0,
            "empty_queries": empties,
            "recall_min": round(min(recalls), 4) if recalls else None,
            "latency_p50_max": max(r["latency_p50_ms"] for r in rows),
        },
        "elapsed_s": round(time.perf_counter() - t_start, 2),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n泄露 {leaked} 条 · 空结果 {empties} 条 · Recall@10 最低 {report['summary']['recall_min']} "
          f"· p50 最高 {report['summary']['latency_p50_max']}ms · 总耗时 {report['elapsed_s']}s")
    print(f"报告 → {out}")


if __name__ == "__main__":
    main()
