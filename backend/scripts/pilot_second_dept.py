"""M14 第二部门真实语料试点：固化「导入双部门 → 评估 → 跨域隔离校验」全流程。

设计目标（用户选择：先用模拟第二部门跑通流程，真实数据到位直接换目录）：
  1. 同一进程内导入两个部门（d_sales / d_marketing）语料到统一检索空间，
     证明多租户隔离不是“分库存”，而是同一索引里按 caller 可见性预筛；
  2. 用第二部门（d_marketing）自己的 golden（标题反查 + viewer=本部门）评估召回；
  3. 构造「跨域泄露探针」：用 d_sales 的身份去查 d_marketing 文档标题，期望全部 drop
     （命中 0），证明跨部门零泄露。

两个部门用不同随机种子生成，正文数值不同 → 不会被 content-hash 去重误杀。
真实试点：把 generate 两段换成 `ingest.import_corpus(真实目录, caller, dept_id=...)` 即可。

用法：
    cd D:/enterprise-ai-platform/backend
    ./.venv/Scripts/python.exe scripts/pilot_second_dept.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gen_pilot_corpus as gp  # noqa: E402

from app.models.caller import CallerContext  # noqa: E402
from app.services import eval as eval_svc, ingest, kb_store  # noqa: E402

DOCS_PER_DEPT = 200
DEPTS = {
    "d_sales": dict(tag="SAL", seed=20261103, scope="department",
                    caller=CallerContext(userId="u_sales_01", deptId="d_sales",
                                        role="employee", tenantId="t_demo")),
    "d_marketing": dict(tag="MKT", seed=7777777, scope="department",
                        caller=CallerContext(userId="u_mkt_01", deptId="d_marketing",
                                            role="employee", tenantId="t_demo")),
}


def generate_dept(dept_id: str, tag: str, seed: int) -> list[tuple[str, str]]:
    """生成单个部门的模拟语料（落盘到 reports/pilot2/<dept>/docs），返回 (family,title) 列表。"""
    out = Path("reports/pilot2") / dept_id / "docs"
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    fams = list(gp.FAMILIES)
    titles: list[tuple[str, str]] = []
    for i in range(DOCS_PER_DEPT):
        family = fams[i % len(fams)]
        title, content = gp.make_doc(family, i, rnd, tag)
        (out / f"{i + 1:03d}.md").write_text(content, encoding="utf-8")
        titles.append((family, title))
    return titles


def main() -> int:
    # 1) 导入两个部门到同一检索空间（同进程，kb_store 内存累计）
    all_titles: dict[str, list[tuple[str, str]]] = {}
    for dept_id, cfg in DEPTS.items():
        titles = generate_dept(dept_id, cfg["tag"], cfg["seed"])
        all_titles[dept_id] = titles
        report = ingest.import_corpus(
            str(Path("reports/pilot2") / dept_id),
            cfg["caller"], scope=cfg["scope"], dept_id=dept_id,
            recursive=True, dry_run=False,
        )
        print(f"  导入 {dept_id}: 新增 {report.get('added', report.get('imported', '?'))} "
              f"篇 / 重复 {report.get('duplicated', '?')} 篇")

    corpus = kb_store.stats() if hasattr(kb_store, "stats") else {}
    print(f"  检索空间：{corpus if corpus else '内存模式（PG 不可达）'}")

    # 2) 第二部门 golden（标题反查 + viewer=本部门）
    mkt = DEPTS["d_marketing"]["caller"]
    golden = []
    for i, (family, title) in enumerate(all_titles["d_marketing"]):
        if i % 5:
            continue
        base = gp.FAMILIES[family][i % len(gp.FAMILIES[family])][0]
        golden.append({
            "query": f"{title.split('（')[0]}的{base}怎么规定",
            "expected_title": title,
            "viewer": {"userId": mkt.userId, "deptId": mkt.deptId,
                       "role": mkt.role, "tenantId": mkt.tenantId},
        })

    print(f"  d_marketing golden：{len(golden)} 条")
    golden, _ = eval_svc.normalize_golden(golden)  # expected_title → expected_doc_id
    report = eval_svc.evaluate(golden=golden, ks=(5, 10, 100))

    # 3) 跨域泄露探针：用 d_sales 身份查 d_marketing 标题（期望全部 drop）
    sales = DEPTS["d_sales"]["caller"]
    probes = []
    for i, (_, title) in enumerate(all_titles["d_marketing"]):
        if i % 10:
            continue
        probes.append({
            "query": f"{title.split('（')[0]}的规定",
            "expected_title": title,
            "viewer": {"userId": sales.userId, "deptId": sales.deptId,
                       "role": sales.role, "tenantId": sales.tenantId},
        })
    probes, _ = eval_svc.normalize_golden(probes)
    probe_report = eval_svc.evaluate(golden=probes, ks=(5,))
    leaked = probe_report["metrics"]["recall@5"]

    # 4) 输出结论
    m = report["metrics"]
    print("─" * 60)
    print(f"第二部门召回：Recall@5 {m['recall@5']} · @10 {m['recall@10']} · @100 {m['recall@100']} · MRR {m['mrr@10']}")
    print(f"过滤正确率：{report['filter_accuracy']}")
    print(f"跨域泄露探针：d_sales 身份查 d_marketing 标题 Recall@5 = {leaked}")
    print(f"隔离结论：{'✅ 零泄露' if leaked == 0 else '❌ 存在泄露'}（期望 0）")
    print(f"门禁：{'PASS ✅' if report['gate']['passed'] else 'FAIL ❌'} — {report['gate']['reason']}")

    eval_svc.write_report(report, "reports/pilot2")
    return 0 if (report["gate"]["passed"] and leaked == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
