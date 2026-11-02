"""生成试点规模模拟语料（用于压测导入链路与验证召回，不是真实业务数据）。

    cd D:/enterprise-ai-platform/backend
    ./.venv/Scripts/python.exe scripts/gen_pilot_corpus.py --out D:/pilot_corpus --docs 220

产物：
    <out>/docs/*.md          220 篇模拟文档（8 个主题族，每篇 300–900 字）
    <out>/golden.jsonl       40 条 golden（query + expected_title，供 eval_baseline 使用）

用途：在没有真实语料的情况下，先证明「批量导入 → 可召回 → 基线跑批 → 成本曲线」这条链
在 200 篇量级下是可跑的、耗时可接受的。真实试点把 docs/ 换成客户原始文档即可。
"""
import argparse
import json
import random
from pathlib import Path

FAMILIES = {
    "销售渠道": [
        ("渠道政策", "一级经销商年度返点 {a}%，二级返点 {b}%。季度超额部分以物料形式返还，窜货三次取消年度返利。"),
        ("经销商准入", "新经销商需提交营业执照、近两年流水与门店照片。审核周期 {a} 个工作日，保证金 {b} 万元。"),
        ("区域保护", "同一商圈内不设第二家同级门店，直线距离不足 {a} 米需报备区域经理裁定。"),
        ("价格体系", "标准零售价 {a} 元，最低成交价不得低于 {b} 折，破价需大区总监书面授权。"),
    ],
    "售后服务": [
        ("工单分级", "P0 故障 {a} 分钟响应、{b} 小时解决；P1 一小时响应、次日闭环；P2 三个工作日跟进。"),
        ("备件管理", "常用备件安全库存 {a} 套，低于阈值自动触发补货，补货周期 {b} 天。"),
        ("退换货规则", "签收 {a} 天内非人为损坏可换新，{b} 天内可退货，定制件不支持无理由退货。"),
    ],
    "财务报销": [
        ("差旅标准", "一线城市住宿上限 {a} 元/晚，其他城市 {b} 元/晚，超标部分自理。"),
        ("报销时限", "费用发生后 {a} 日内提交，跨月单据需附说明，每月 {b} 日截止当月批次。"),
        ("付款审批", "合同金额 {a} 万以下主管审批，{a}–{b} 万总监审批，超过 {b} 万需 CEO 签批。"),
    ],
    "人力资源": [
        ("考勤制度", "核心工时 {a}:00–{b}:00 须在岗，弹性上下班，月度迟到超过三次计入绩效。"),
        ("招聘流程", "面试分业务、主管、HR 三轮，背调在 offer 前完成，offer 审批 {a} 个工作日。"),
        ("晋升评审", "每年 {a} 月与 {b} 月两次评审，需连续两个季度绩效达标并由直属主管提名。"),
    ],
    "产品交付": [
        ("交付标准", "交付物包含源文件、切图与说明文档，命名按项目编号，交付前过自检清单。"),
        ("版本节奏", "双周一个迭代，{a} 周封版，{b} 周回归测试，紧急修复走 hotfix 通道。"),
        ("验收流程", "客户验收期 {a} 个工作日，验收问题按严重级排期，阻塞项 {b} 小时内响应。"),
    ],
    "合规安全": [
        ("数据分级", "客户数据分为公开、内部、机密三级，机密数据禁止外发，传输须加密。"),
        ("权限回收", "离职当日回收全部系统权限，转岗 {a} 个工作日内完成权限重配。"),
        ("审计留痕", "关键操作保留 {a} 天日志，财务与权限变更日志保留 {b} 年。"),
    ],
    "物流仓储": [
        ("发货时效", "当日 {a} 点前下单当日发出，偏远地区顺延，大件走专线物流。"),
        ("库存盘点", "每月 {a} 日全盘，每周抽盘，差异超过 {b}% 需出具说明。"),
        ("破损理赔", "签收 {a} 小时内报损并留存开箱视频，赔付按货值 {b}% 执行。"),
    ],
    "市场品牌": [
        ("物料规范", "对外物料统一使用品牌主色与指定字体，发布前须经品牌部审核。"),
        ("活动审批", "预算 {a} 万以下部门审批，{a}–{b} 万市场部审批，超过 {b} 万上会。"),
        ("舆情响应", "负面舆情 {a} 小时内上报，{b} 小时内给出统一口径，禁止个人对外回应。"),
    ],
}

SCENES = ["华东大区", "华南大区", "华北区域", "西南片区", "线上渠道", "直营门店", "经销体系", "大客户部"]


def make_doc(family: str, idx: int, rnd: random.Random, tag: str = "") -> tuple[str, str]:
    topics = FAMILIES[family]
    title_base, body_tpl = topics[idx % len(topics)]
    scene = SCENES[idx % len(SCENES)]
    a, b = rnd.randint(2, 60), rnd.randint(3, 98)
    body = body_tpl.format(a=a, b=b)
    extra = (
        f"\n\n适用范围：{scene}。本文件自 2026 年 {rnd.randint(1, 12)} 月起执行，"
        f"由{family}负责人维护，解释权归平台运营中心。如与总部制度冲突，以总部制度为准。"
        f"\n\n执行要点：\n1. 责任到人，{scene}负责人为第一责任人；\n"
        f"2. 每月复盘一次执行偏差，偏差超过 {rnd.randint(2, 9)}% 需书面说明；\n"
        f"3. 相关单据留存 {rnd.randint(1, 3)} 年备查。"
        f"\n\n常见问题：{title_base}在{scene}落地时，最常见的问题是口径不统一。"
        f"统一口径的做法是先对齐定义，再对齐数据源，最后对齐考核。第 {idx + 1} 版补充了跨部门协作条款。"
    )
    title = f"{scene}{title_base}（{2026}-{idx + 1:03d}{('-' + tag) if tag else ''}）"
    return title, f"# {title}\n\n{body}{extra}"


def main() -> None:
    ap = argparse.ArgumentParser(description="生成试点规模模拟语料")
    ap.add_argument("--out", default="D:/pilot_corpus")
    ap.add_argument("--docs", type=int, default=220)
    ap.add_argument("--seed", type=int, default=20261102)
    ap.add_argument("--tag", default="", help="标题后缀标记（如 D5），避免与既有语料标题重名")
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    out = Path(args.out)
    docs_dir = out / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    fams = list(FAMILIES)
    titles: list[tuple[str, str]] = []
    for i in range(args.docs):
        family = fams[i % len(fams)]
        title, content = make_doc(family, i, rnd, args.tag)
        (docs_dir / f"{i + 1:03d}_{family}_{title[:20]}.md".replace("/", "-")).write_text(
            content, encoding="utf-8")
        titles.append((family, title))

    # golden：每个主题族抽 5 条，问题从正文里可判定的关键词构造
    golden = []
    for i, (family, title) in enumerate(titles):
        if i % 5:
            continue
        base = FAMILIES[family][i % len(FAMILIES[family])][0]
        q = rnd.choice([
            f"{title.split('（')[0]}的{base}怎么规定",
            f"{base}在{title.split('（')[0]}怎么执行",
            f"{title.split('（')[0]} {base} 要求是什么",
        ])
        golden.append({"query": q, "expected_title": title, "scope": "department"})
    if args.tag:
        print(f"（带 tag={args.tag}：仅生成语料，不覆盖 golden.jsonl）")
        print(f"已生成 {args.docs} 篇 → {docs_dir}")
        return
    (out / "golden.jsonl").write_text(
        "# 模拟 golden：仅用于验证跑批链路，不代表真实业务查询分布\n"
        + "\n".join(json.dumps(g, ensure_ascii=False) for g in golden),
        encoding="utf-8")

    print(f"已生成 {args.docs} 篇 → {docs_dir}")
    print(f"golden {len(golden)} 条 → {out / 'golden.jsonl'}")


if __name__ == "__main__":
    main()
