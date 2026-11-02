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
import time
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


def _filter_probes() -> list[dict]:
    """当前语料里「张三（销售部员工）无权看」的文档，作为越权探针。

    旧实现写死了 4 条演示探针，关掉演示语料（M8 试点 SEED_DEMO_CORPUS=0）后
    会误判成 0 分——正确的口径应该是：有多少越权样本就检查多少，没有样本时不算失败。
    """
    probes: list[dict] = []
    for p in knowledge.get_parents():
        if p.get("tenant_id") != "t_demo":
            probes.append(p)                                   # 跨租户
        elif p.get("scope") == "department" and p.get("dept") != "d_sales":
            probes.append(p)                                   # 跨部门
        elif p.get("scope") == "personal" and p.get("owner") != "u_1024":
            probes.append(p)                                   # 他人私有
    return probes


def compute_filter_accuracy() -> float:
    """第18章过滤点正确率：越权文档一条都不能进可见结果（探针随语料自适应）。"""
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
    probes = _filter_probes()[:12]
    if not probes:
        return 1.0          # 无越权样本 → 无从泄露，不判失败（但报告里会标注探针为 0）
    passed = 0
    for p in probes:
        hits, _ = knowledge.retrieve(p["title"], caller, top_k=10)
        if all(h["doc_id"] != p["doc_id"] for h in hits):
            passed += 1
    return passed / len(probes)


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


# ─────────────────────────────────────────────────────────────────────────────
# M8 基线跑批（试点：真语料 + 真 golden，每天跑一次看召回漂移）
# ─────────────────────────────────────────────────────────────────────────────
REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _default_viewer(doc_id: str, tenant_id: str = "t_demo") -> dict:
    p = knowledge.meta_of(doc_id)
    if p:
        return _viewer_for(p)
    return {"userId": "u_admin_01", "deptId": "d_it", "role": "platform_admin", "tenantId": tenant_id}


def normalize_golden(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """外部 golden 归一化：允许只写 query + expected_title（真实语料的 doc_id 是随机的，
    试点同事不可能知道 doc_3f8a1c02 对应哪篇）。

    返回 (可用条目, 丢弃条目+原因)。丢弃不静默——试点最怕 golden 写错了但报告很漂亮。
    """
    ok: list[dict] = []
    bad: list[dict] = []
    for i, e in enumerate(entries, start=1):
        if not isinstance(e, dict):
            bad.append({"line": i, "reason": "不是对象"})
            continue
        q = (e.get("query") or "").strip()
        doc_id = e.get("expected_doc_id") or ""
        if not doc_id and e.get("expected_title"):
            doc_id = knowledge.find_by_title(str(e["expected_title"])) or ""
        if not q:
            bad.append({"line": i, "reason": "query 为空"})
            continue
        if not doc_id:
            bad.append({"line": i,
                        "reason": f"找不到目标文档：{e.get('expected_title') or e.get('expected_doc_id') or '(未指定)'}"})
            continue
        item = {
            "query": q,
            "expected_doc_id": doc_id,
            "viewer": e.get("viewer") or _default_viewer(doc_id, e.get("tenant_id") or "t_demo"),
        }
        # 多目标（M9）：同一族文档往往都能回答同一个问题（「渠道政策怎么执行」
        # 对 2026-041 / 2026-025 都成立），只标一个 doc_id 会把「召回到同族另一篇」
        # 算成漏检，让指标系统性偏低。allow 里任一命中即算召回。
        accept = []
        for t in (e.get("accept_titles") or []):
            did = knowledge.find_by_title(str(t))
            if did and did != doc_id and did not in accept:
                accept.append(did)
        if accept:
            item["accept_doc_ids"] = accept
        scope = e.get("scope") or (knowledge.meta_of(doc_id) or {}).get("scope")
        if scope:
            item["scope"] = scope
        ok.append(item)
    return ok, bad


def load_golden_file(path: str | Path) -> list[dict]:
    """读外部 golden：.jsonl 每行一条（# 开头为注释），或 .json 数组 / {queries:[...]}。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"golden 文件不存在：{p}")
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return data.get("queries") or data.get("entries") or []
    rows: list[dict] = []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"golden 第 {n} 行 JSON 解析失败：{e}")
    return rows


def compute_metrics(golden: list[dict], ks: tuple[int, ...] = METRIC_KS) -> dict:
    """一次检索算全部指标。

    原实现每个指标各检索一遍（200 条 golden = 600 次检索，试点规模下太慢）；
    这里只检索一次，按排名一次性累加 recall/mrr/ndcg 与分组。
    """
    ks = tuple(sorted({int(k) for k in ks}))
    max_k = max(ks)
    hit = {k: 0 for k in ks}
    ndcg = {k: 0.0 for k in ks}
    rr_sum = 0.0
    by_scope: dict[str, dict] = {}
    for g in golden:
        caller = CallerContext(**g["viewer"])
        hits, _ = knowledge.retrieve(g["query"], caller, top_k=max_k)
        ids = [h["doc_id"] for h in hits]
        target = g["expected_doc_id"]
        # 多目标命中：取所有可接受文档里排名最靠前的那一个
        targets = [target] + list(g.get("accept_doc_ids") or [])
        ranks = [ids.index(t) + 1 for t in targets if t in ids]
        rank = min(ranks) if ranks else 0
        scope = g.get("scope") or "unknown"
        s = by_scope.setdefault(scope, {"queries": 0, "hits": {k: 0 for k in ks}, "rr": 0.0})
        s["queries"] += 1
        if rank:
            rr_sum += 1.0 / rank
            s["rr"] += 1.0 / rank
            for k in ks:
                if rank <= k:
                    hit[k] += 1
                    s["hits"][k] += 1
                    ndcg[k] += 1.0 / math.log2(rank + 1)
    n = len(golden) or 1
    metrics: dict[str, float] = {}
    for k in ks:
        metrics[f"recall@{k}"] = round(hit[k] / n, 4)
    metrics["mrr@10"] = round(rr_sum / n, 4)
    ndcg_k = 10 if 10 in ks else max_k
    metrics[f"ndcg@{ndcg_k}"] = round(ndcg[ndcg_k] / n, 4)
    metrics["golden_size"] = len(golden)

    scope_rows = {}
    for sc, s in sorted(by_scope.items()):
        scope_rows[sc] = {
            "queries": s["queries"],
            "recall@10": round(s["hits"].get(10 if 10 in ks else max_k) / s["queries"], 4),
            "recall@100": round(s["hits"][max_k] / s["queries"], 4),
            "mrr": round(s["rr"] / s["queries"], 4),
        }
    return {"metrics": metrics, "by_scope": scope_rows}


def evaluate(golden: list[dict] | None = None, ks: tuple[int, ...] = METRIC_KS,
             golden_path: str | Path | None = None, with_filter: bool = True) -> dict:
    """跑一次完整基线：指标 + 分组 + 过滤正确率 + 门禁结论 + 语料规模。"""
    import time as _t

    t0 = _t.time()
    dropped: list[dict] = []
    if golden is None:
        raw = load_golden_file(golden_path) if golden_path else (load_golden() or build_golden_entries())
        golden, dropped = normalize_golden(raw)
    if not golden:
        raise ValueError("golden 为空：没有可评测的 query（检查 expected_title 是否与语料标题一致）")

    res = compute_metrics(golden, ks)
    fa = round(compute_filter_accuracy(), 4) if with_filter else None
    r100 = res["metrics"].get("recall@100", 0.0)
    filter_passed = (fa is None) or (fa >= 1.0)
    passed = r100 >= RECALL_GATE_THRESHOLD and filter_passed

    corpus = knowledge.corpus_size()
    warnings: list[str] = []
    external = corpus["documents"] - corpus.get("demo_documents", 0)
    if not golden_path and external > 0:
        warnings.append(
            f"当前有 {external} 篇外部语料与 {corpus.get('demo_documents', 0)} 篇演示语料共同参与检索；"
            f"内置 golden 是基于演示语料构造的，指标只代表混合环境，试点结论请用自带 golden（--golden）")
    if fa is not None and not _filter_probes():
        warnings.append("当前语料中没有跨租户/跨部门/他人私有样本，过滤正确率记为 1.0（无从泄露）")

    return {
        "generated_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "golden_source": str(golden_path) if golden_path else ("builtin:" + ("file" if GOLDEN_PATH.exists() else "memory")),
        "ks": list(ks),
        "golden_size": len(golden),
        "dropped": dropped,
        "metrics": res["metrics"],
        "by_scope": res["by_scope"],
        "filter_accuracy": fa,
        "filter_probes": len(_filter_probes()) if with_filter else None,
        "warnings": warnings,
        "gate": {
            "k": RECALL_GATE_K,
            "threshold": RECALL_GATE_THRESHOLD,
            "recall": r100,
            "filter_accuracy": fa,
            "passed": passed,
            "reason": "通过" if passed else (
                f"Recall@{RECALL_GATE_K}={r100} < {RECALL_GATE_THRESHOLD}"
                if r100 < RECALL_GATE_THRESHOLD else f"过滤正确率={fa} < 1.0"),
        },
        "corpus": corpus,
        "duration_ms": round((_t.time() - t0) * 1000, 1),
    }


def write_report(report: dict, out_dir: str | Path | None = None,
                 name: str = "baseline") -> dict:
    """落盘：带时间戳快照 + latest.json（页面读这个）+ history.jsonl（漂移追踪）+ 人读 md。"""
    out = Path(out_dir) if out_dir else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = json.dumps(report, ensure_ascii=False, indent=2)
    (out / f"{name}-{stamp}.json").write_text(body, encoding="utf-8")
    (out / f"{name}.json").write_text(body, encoding="utf-8")
    (out / "latest.json").write_text(body, encoding="utf-8")

    m = report["metrics"]
    row = {"at": report["generated_at"], "name": name,
           "golden_size": report["golden_size"],
           "recall@10": m.get("recall@10"), "recall@100": m.get("recall@100"),
           "mrr@10": m.get("mrr@10"), "ndcg@10": m.get("ndcg@10"),
           "filter_accuracy": report.get("filter_accuracy"),
           "documents": (report.get("corpus") or {}).get("documents"),
           "passed": report["gate"]["passed"]}
    with (out / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    lines = [
        f"# RAG 基线评测报告 · {report['generated_at']}",
        "",
        f"数据源：{report['golden_source']}　golden {report['golden_size']} 条"
        f"（丢弃 {len(report['dropped'])} 条）　语料 {(report.get('corpus') or {}).get('documents', 0)} 篇"
        f" / {(report.get('corpus') or {}).get('children', 0)} 子块　耗时 {report['duration_ms']} ms",
        "",
        "## 门禁结论",
        "",
        f"**{'通过 ✅' if report['gate']['passed'] else '未通过 ❌'}** — {report['gate']['reason']}"
        f"（第36章 Recall@{report['gate']['k']} ≥ {report['gate']['threshold']}"
        f"；第37章 过滤正确率 = 1.0）",
        "",
        "## 指标",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
    ]
    for k in ("recall@5", "recall@10", "recall@100", "mrr@10", "ndcg@10"):
        if k in m:
            lines.append(f"| {k} | {m[k]} |")
    lines += [f"| filter_accuracy | {report.get('filter_accuracy')} |", "",
              f"越权探针 {report.get('filter_probes')} 条（跨租户 / 跨部门 / 他人私有，随语料自适应；"
              f"0 条表示当前语料无越权样本，不判失败）", "",
              "## 按知识范围分组", "",
              "| scope | queries | Recall@10 | Recall@100 | MRR |", "| --- | --- | --- | --- | --- |"]
    for sc, s in (report.get("by_scope") or {}).items():
        lines.append(f"| {sc} | {s['queries']} | {s['recall@10']} | {s['recall@100']} | {s['mrr']} |")
    if report.get("warnings"):
        lines += ["", "## 口径提示", ""]
        lines += [f"- {w}" for w in report["warnings"]]
    if report["dropped"]:
        lines += ["", "## 丢弃的 golden 条目（需修正）", "", "| 行 | 原因 |", "| --- | --- |"]
        lines += [f"| {d['line']} | {d['reason']} |" for d in report["dropped"][:20]]
    mp = out / f"{name}.md"
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(out / f"{name}.json"), "snapshot": str(out / f"{name}-{stamp}.json"),
            "markdown": str(mp), "history": str(out / "history.jsonl")}


def latest_report(out_dir: str | Path | None = None) -> dict | None:
    p = (Path(out_dir) if out_dir else REPORT_DIR) / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def history(out_dir: str | Path | None = None, limit: int = 30) -> list[dict]:
    """基线漂移序列（试点每天跑一次，看召回有没有随语料增长掉下去）。"""
    p = (Path(out_dir) if out_dir else REPORT_DIR) / "history.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="企业 AI 中台 · 评测门禁（第36/37章）")
    parser.add_argument("mode", choices=["recall", "objective", "baseline"], nargs="?", default="recall")
    parser.add_argument("--k", type=int, default=RECALL_GATE_K, help="Recall@K 门禁的 K")
    parser.add_argument("--golden", default="", help="外部 golden 路径（.jsonl / .json）")
    parser.add_argument("--out-dir", default="", help="报告输出目录（默认 backend/reports）")
    parser.add_argument("--no-filter", action="store_true", help="跳过过滤正确率（快一些）")
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
    else:
        report = evaluate(golden_path=args.golden or None, with_filter=not args.no_filter)
        paths = write_report(report, args.out_dir or None)
        print(f"[BASELINE] golden={report['golden_size']} 语料={report['corpus']['documents']} 篇 "
              f"召回@100={report['metrics'].get('recall@100')} mrr@10={report['metrics'].get('mrr@10')}")
        print(f"[BASELINE] 门禁：{'PASS' if report['gate']['passed'] else 'FAIL'} — {report['gate']['reason']}")
        print(f"[BASELINE] 报告：{paths['json']}")
        if not report["gate"]["passed"]:
            raise SystemExit(f"FAIL: baseline gate — {report['gate']['reason']}")


if __name__ == "__main__":
    main()
