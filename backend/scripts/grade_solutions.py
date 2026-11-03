"""M15 · 技能包质量评估（修正版）。

前一版用「正文字数 < 600」判定模板壳，实测误判（最薄包仍含 4 步流程与量价分离公式）。
本版改用结构信号判定，更贴近「能否作为生产智能体」：

    has_workflow   有序步骤 >= 3 步          （能按步执行）
    has_inputs     有输入契约                （知道吃什么）
    has_outputs    有产出契约                （知道交什么）
    has_rules      有硬规则/红线             （知道不能干什么）
    has_formula    含量化口径/公式           （数字可验算，不靠嘴）

分级：
    production   4 项以上（含 workflow+inputs）→ 可直接注册上生产
    usable       含 workflow 或（inputs+outputs）→ 补规则后可用
    skeleton     缺流程也缺契约 → 必须人工重写
"""
from __future__ import annotations

import collections
import json
import re

FORMULA_PAT = re.compile(r"[=＝]|×|÷|÷|占比|口径|分母|分子|公式|阈值|≤|≥|<=|>=")


def grade(defs: list[dict]) -> list[dict]:
    out = []
    for d in defs:
        sec = d.get("sections", [])
        flags = {
            "has_workflow": d.get("has_workflow", False),
            "has_inputs": "inputs" in sec,
            "has_outputs": "outputs" in sec,
            "has_rules": d.get("has_rules", False),
        }
        sp = d.get("system_prompt", "")
        flags["has_formula"] = bool(FORMULA_PAT.search(sp))
        score = sum(1 for v in flags.values() if v)
        if flags["has_workflow"] and flags["has_inputs"] and score >= 4:
            g = "production"
        elif flags["has_workflow"] or (flags["has_inputs"] and flags["has_outputs"]):
            g = "usable"
        else:
            g = "skeleton"
        out.append({**d, "quality": g, "signal_score": score, **flags})
    return out


def summarize(out: list[dict]) -> None:
    by = collections.defaultdict(lambda: collections.Counter())
    for d in out:
        key = d["board_cn"] + (f"/{d['sub_function']}" if d["sub_function"] else "")
        by[key][d["quality"]] += 1
    print(f"{'板块':20s} {'总数':>4s} {'production':>10s} {'usable':>7s} {'skeleton':>8s}")
    print("-" * 56)
    tot = collections.Counter()
    for k in sorted(by):
        c = by[k]
        n = sum(c.values())
        print(f"{k:20s} {n:4d} {c['production']:10d} {c['usable']:7d} {c['skeleton']:8d}")
        tot.update(c)
    print("-" * 56)
    print(f"{'合计':20s} {sum(tot.values()):4d} {tot['production']:10d} {tot['usable']:7d} {tot['skeleton']:8d}")
    print(f"\n信号覆盖：workflow {sum(1 for d in out if d['has_workflow'])}/"
          f"{len(out)}  inputs {sum(1 for d in out if d['has_inputs'])}/"
          f"{len(out)}  outputs {sum(1 for d in out if d['has_outputs'])}/"
          f"{len(out)}  rules {sum(1 for d in out if d['has_rules'])}/"
          f"{len(out)}  formula {sum(1 for d in out if d['has_formula'])}/{len(out)}")
    sk = [d for d in out if d["quality"] == "skeleton"]
    if sk:
        print(f"\n需人工重写的 {len(sk)} 个：")
        for d in sk:
            print(f"  {d['board_cn']}/{d['name']}  (章节: {','.join(d['sections']) or '无'})")


def main() -> int:
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "reports/defs.json"
    defs = json.load(open(src, encoding="utf-8"))
    out = grade(defs)
    summarize(out)
    dst = src.replace(".json", "_graded.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已导出：{dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
