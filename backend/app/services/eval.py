"""评测服务（第36章 RAG 召回门禁 + 第37章 客观评测）。

CI 门禁：Recall@100 >= 0.95（第36章）；第37章客观指标（准确率/MRR/nDCG/过滤正确率）
不依赖 LLM-as-judge，全部由确定性检索结果计算。

golden 评测集：M2 W3 构建，>=200 条 golden queries（每条含 query + expected_doc_id）。
优先读取 services/golden.jsonl；缺失时由 build_golden_entries() 内存重建（保证 CI 可跑）。
"""
import json
import math
import re
import sys
from pathlib import Path

from app.models.caller import CallerContext
from app.services import knowledge

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"
RECALL_GATE_K = 100
RECALL_GATE_THRESHOLD = 0.95
METRIC_KS = (5, 10, 100)


# ─────────────────────────────────────────────────────────────────────────────
# golden 评测集（单一数据源：内存生成，gen_golden.py 落盘同一逻辑）
# ─────────────────────────────────────────────────────────────────────────────
def _viewer_for(p: dict) -> dict:
    """为该文档生成「有权限查看」的 caller（不绕过第18章过滤点，合法评测）。"""
    scope = p["scope"]
    if scope == "global":
        return {"userId": "u_admin_01", "deptId": "d_hr", "role": "platform_admin", "tenantId": p["tenant_id"]}
    if scope == "department":
        return {"userId": "u_admin_01", "deptId": p["dept"], "role": "dept_admin", "tenantId": p["tenant_id"]}
    return {"userId": p["owner"], "deptId": p["dept"], "role": "employee", "tenantId": p["tenant_id"]}


def build_golden_entries() -> list[dict]:
    """基于知识库语料生成 golden（每条含 query + expected_doc_id + viewer）。

    viewer 为对该文档有权限的角色，确保召回评测在「权限内」验证检索质量，
    而不被第18章过滤点误判为未召回。
    """
    entries: list[dict] = []
    for p in knowledge.get_parents():
        viewer = _viewer_for(p)
        t = p["title"]
        c = p["content"]
        kw = re.findall(r"[一-鿿]{2,4}", t + c)[:8]
        seen: set[str] = set()
        qs: list[str] = []

        def add(q: str) -> None:
            if q not in seen:
                seen.add(q)
                qs.append(q)

        add(t)
        for k in kw:
            add(k)
            add(f"{t} 中的{k}")
            add(f"{k}怎么处理")
        add(f"关于{t}的说明")
        add(f"公司{t}的规定")
        add(f"{t}如何执行")
        for q in qs[:14]:
            entries.append({"query": q, "expected_doc_id": p["doc_id"], "viewer": viewer})
    return entries


def load_golden() -> list[dict] | None:
    if GOLDEN_PATH.exists():
        rows = []
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    return None


def compute_recall_at_k(golden: list[dict], k: int) -> float:
    """每条 golden 验证 expected_doc_id 是否进入 top_k 可见结果（以该文档 viewer 检索）。"""
    hit = 0
    for g in golden:
        caller = CallerContext(**g["viewer"])
        hits, _ = knowledge.retrieve(g["query"], caller, top_k=k)
        if any(h["doc_id"] == g["expected_doc_id"] for h in hits):
            hit += 1
    return hit / len(golden) if golden else 0.0


def compute_mrr_at_k(golden: list[dict], k: int) -> float:
    """平均倒数排名：首个 expected 在 ranked 可见结果中的排名倒数。"""
    rr = 0.0
    for g in golden:
        caller = CallerContext(**g["viewer"])
        hits, _ = knowledge.retrieve(g["query"], caller, top_k=k)
        for rank, h in enumerate(hits, start=1):
            if h["doc_id"] == g["expected_doc_id"]:
                rr += 1.0 / rank
                break
    return rr / len(golden) if golden else 0.0


def compute_ndcg_at_k(golden: list[dict], k: int) -> float:
    """nDCG@K：相关=1（命中 expected），否则 0。DCG/IDCG。"""
    total = 0.0
    for g in golden:
        caller = CallerContext(**g["viewer"])
        hits, _ = knowledge.retrieve(g["query"], caller, top_k=k)
        dcg = 0.0
        for rank, h in enumerate(hits, start=1):
            rel = 1.0 if h["doc_id"] == g["expected_doc_id"] else 0.0
            dcg += rel / math.log2(rank + 1)
        total += dcg  # IDCG=1（单相关），故 nDCG=DCG
    return total / len(golden) if golden else 0.0


def compute_filter_accuracy() -> float:
    """第18章过滤点正确率：跨租户/跨部门 query 不得让越权片段进入可见结果。"""
    # 张三（销售部）查设计部/友商租户内容，应被过滤
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
    probes = ["设计部品牌规范", "友商租户文档", "视觉素材库使用说明", "设计稿交付标准"]
    checks = 0
    passed = 0
    for q in probes:
        hits, filtered_out = knowledge.retrieve(q, caller, top_k=10)
        checks += 1
        leak = any(h["scope"] == "department" and h.get("dept") != "d_sales"
                   or h.get("tenant_id") != "t_demo" for h in hits)
        if (not leak) and filtered_out >= 1:
            passed += 1
    return passed / checks if checks else 0.0


def run_recall_gate(k: int = RECALL_GATE_K) -> float:
    golden = load_golden() or build_golden_entries()
    score = compute_recall_at_k(golden, k)
    print(f"Recall@{k} = {score:.3f}  (门禁阈值 >= {RECALL_GATE_THRESHOLD})")
    if score < RECALL_GATE_THRESHOLD:
        raise SystemExit(f"FAIL: Recall@{k} {score:.3f} < {RECALL_GATE_THRESHOLD}")
    return score


def run_objective_gate() -> dict:
    """第37章客观指标：不调用 LLM 打分。"""
    golden = load_golden() or build_golden_entries()
    metrics = {
        "recall@5": round(compute_recall_at_k(golden, 5), 3),
        "recall@10": round(compute_recall_at_k(golden, 10), 3),
        "recall@100": round(compute_recall_at_k(golden, 100), 3),
        "mrr@10": round(compute_mrr_at_k(golden, 10), 3),
        "ndcg@10": round(compute_ndcg_at_k(golden, 10), 3),
        "filter_accuracy": round(compute_filter_accuracy(), 3),
        "golden_size": len(golden),
    }
    print("[OBJECTIVE] 客观评测指标（不依赖 LLM-as-judge）：")
    for kk, vv in metrics.items():
        print(f"  {kk}: {vv}")
    return {"status": "passed" if metrics["recall@100"] >= RECALL_GATE_THRESHOLD
            and metrics["filter_accuracy"] >= 1.0 else "review", "metrics": metrics}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="企业 AI 中台 · 评测门禁（第36/37章）")
    parser.add_argument("mode", choices=["recall", "objective"], nargs="?", default="recall")
    parser.add_argument("--k", type=int, default=RECALL_GATE_K, help="Recall@K 门禁的 K")
    args = parser.parse_args()

    if args.mode == "recall":
        run_recall_gate(k=args.k)
    elif args.mode == "objective":
        res = run_objective_gate()
        # 第37章：客观指标不达标 → 非零退出，阻断 CI / 演化（第20章联动）
        if res["status"] != "passed":
            raise SystemExit(
                f"FAIL: objective gate status={res['status']} "
                f"(recall@100>={RECALL_GATE_THRESHOLD}, filter_accuracy>=1.0)"
            )


if __name__ == "__main__":
    main()
