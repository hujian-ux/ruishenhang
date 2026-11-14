#!/usr/bin/env python3
"""办公62 硬规则 —— 生产级命中率测量台。

数据源（两份，均落 backend/data/）：
    1) office62_captures.jsonl  —— 生产日志（与 invoke.py OFFICE62_CAPTURE 钩子同构；
       线上真实产出物落盘，或 office62_capture_seed.py 生成的真实文件字节样本）。
       本台对每条记录用「当前规则」重新扫描（scan_office62_blob），因此规则一改，
       命中率立即反映「规则漂移」，无需重跑采集。
   2) office62_eval.jsonl       —— 带 ground-truth 标注的评测集（benign / expect 规则）。
       仅用于离线计算召回率 / 精确率 / 误报率；线上真实日志没有标注时自动跳过。

产出指标：
   生产监控（无需标注）：总样本、各 scope 分布、合规率（零 ≥medium 占比）、
       high 级拦截率、严重度分布、Top 命中规则、死规则（从未触发）。
   标注评测（有 eval 文件时）：红线召回率、违规检测率(≥中危)、合规误报率(≥中危)、
       中危+预测精确率、漏检明细。

诚实声明：captures.jsonl 可直接来自两条路径——(1) seeder 生成的真实办公文件字节样本，
比纯文本合成更贴近生产；(2) 线上真实日志：演示后端开 OFFICE62_CAPTURE=1 跑一段时间后，
invoke 链路会把真实产出物文本落盘到同构的 captures.jsonl，直接喂本台即为生产级命中率。
（已实测：19 条真实 LLM 办公产出物经本台重扫，O62-026/O62-033 升 medium 后分居 Top1/Top2。）

用法：
    PYTHONPATH=. python scripts/office62_prod_hitrate.py
    PYTHONPATH=. python scripts/office62_prod_hitrate.py --captures data/office62_captures.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from app.services import guardrails

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CAPTURE_PATH = os.path.join(_DATA_DIR, "office62_captures.jsonl")
_EVAL_PATH = os.path.join(_DATA_DIR, "office62_eval.jsonl")

_SEV_RANK = {"low": 1, "medium": 2, "high": 3}


def load_jsonl(path: str) -> list[dict]:
    out: list[dict] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", default=_CAPTURE_PATH, help="生产日志 jsonl 路径")
    ap.add_argument("--eval", default=_EVAL_PATH, help="标注评测集 jsonl 路径")
    args = ap.parse_args()

    info = guardrails.load_office62_rules(force=True)
    total_rules = len(info["rules"])
    caps = load_jsonl(args.captures)
    evals = load_jsonl(args.eval)

    print(f"规则文件    : {info['path']}")
    print(f"加载规则数  : {total_rules}   加载错误: {info['error'] or '无'}")
    print(f"生产日志    : {args.captures}  ({len(caps)} 条)")
    print(f"评测标注    : {args.eval}  ({len(evals)} 条)\n")

    if not caps:
        print("生产日志为空，无法测量。先跑 office62_capture_seed.py 生成样本日志。")
        return 1

    # ── 生产监控：用「当前规则」重扫每条捕获 ──
    scope_counter: Counter = Counter()
    sev_counter: Counter = Counter()
    rule_counter: Counter = Counter()
    blocked_count = 0
    compliant_count = 0          # 零 ≥medium 违规
    total = len(caps)
    fired_rules: set[str] = set()

    for rec in caps:
        scope = rec.get("scope")
        texts = rec.get("texts") or []
        scan = guardrails.scan_office62_blob(texts, scope=scope)
        scope_counter[scope or "global"] += 1
        for v in scan["violations"]:
            sev_counter[v["severity"]] += 1
            rule_counter[v["id"]] += 1
            fired_rules.add(v["id"])
        if scan["blocked"]:
            blocked_count += 1
        if not any(v["severity"] in ("medium", "high") for v in scan["violations"]):
            compliant_count += 1

    compliance_rate = compliant_count / total if total else 0.0
    block_rate = blocked_count / total if total else 0.0

    print("=" * 68)
    print("一、生产监控指标（对捕获日志用当前规则重扫）")
    print("=" * 68)
    print(f"  捕获总条数        : {total}")
    print(f"  各 scope 分布     : " + ", ".join(
        f"{k}={v}" for k, v in sorted(scope_counter.items())))
    print(f"  合规率(零≥中危)   : {compliance_rate*100:.1f}%   ({compliant_count}/{total})")
    print(f"  high 拦截率       : {block_rate*100:.1f}%   ({blocked_count}/{total})")
    print(f"  严重度分布        : high={sev_counter['high']}  medium={sev_counter['medium']}  low={sev_counter['low']}")
    print(f"  规则触发覆盖率    : {len(fired_rules)}/{total_rules}  ({len(fired_rules)/total_rules*100:.1f}%)")

    print("\n  Top 命中规则（按触发次数）：")
    for rid, cnt in rule_counter.most_common(12):
        print(f"    {rid:<10} {cnt:>4} 次")

    all_ids = {r["id"] for r in info["rules"]}
    dead = sorted(all_ids - fired_rules)
    print(f"\n  本日志未触发规则（{len(dead)} 条，死规则/取样不足）：")
    print("    " + (", ".join(dead) if dead else "（无）"))

    # ── 标注评测：召回 / 精确 / 误报 ──
    if not evals:
        print("\n" + "=" * 68)
        print("二、标注评测")
        print("=" * 68)
        print("  无评测标注文件（线上真实日志无标注）。仅完成生产监控。")
        print("  若要计算召回率/精确率，请先跑 office62_capture_seed.py 生成 eval 标注。")
        return 0

    tp = 0
    fn_total = 0
    benign = [e for e in evals if e.get("benign")]
    violation = [e for e in evals if not e.get("benign")]
    pred_pos = []  # (eval, scan) 含 ≥medium
    for e in evals:
        scope = guardrails.office62_scope_for_agent({"default_format": e.get("fmt")})
        texts = e.get("texts") or []
        scan = guardrails.scan_office62_blob(texts, scope=scope)
        detected = {v["id"] for v in scan["violations"]}
        if any(v["severity"] in ("medium", "high") for v in scan["violations"]):
            pred_pos.append((e, scan))
        for exp in (e.get("expect") or []):
            fn_total += 1
            if exp in detected:
                tp += 1
    recall = tp / fn_total if fn_total else 0.0

    benign_fp = [(e, scan) for e, scan in
                 [(e, guardrails.scan_office62_blob(e.get("texts") or [],
                                                   scope=guardrails.office62_scope_for_agent(
                                                       {"default_format": e.get("fmt")})))
                  for e in benign]
                 if any(v["severity"] in ("medium", "high") for v in scan["violations"])]
    benign_fp_rate = len(benign_fp) / len(benign) if benign else 0.0

    tp_samples = sum(1 for e, _ in pred_pos if not e.get("benign"))
    fp_samples = sum(1 for e, _ in pred_pos if e.get("benign"))
    precision = tp_samples / (tp_samples + fp_samples) if (tp_samples + fp_samples) else 0.0

    viol_detected = sum(1 for e, scan in
                        [(e, guardrails.scan_office62_blob(e.get("texts") or [],
                                                          scope=guardrails.office62_scope_for_agent(
                                                              {"default_format": e.get("fmt")})))
                         for e in violation]
                        if any(v["severity"] in ("medium", "high") for v in scan["violations"]))
    viol_detect_rate = viol_detected / len(violation) if violation else 0.0

    print("\n" + "=" * 68)
    print("二、标注评测指标（基于 ground-truth 标注集）")
    print("=" * 68)
    print(f"  样本总数          : {len(evals)}  (合规 {len(benign)} / 违规 {len(violation)})")
    print(f"  红线召回率        : {recall*100:.1f}%   ({tp}/{fn_total} 期望规则被命中)")
    print(f"  违规检测率(≥中危) : {viol_detect_rate*100:.1f}%  ({viol_detected}/{len(violation)})")
    print(f"  合规误报率(≥中危) : {benign_fp_rate*100:.1f}%   ({len(benign_fp)}/{len(benign)} 合规被误伤)")
    print(f"  中危+预测精确率   : {precision*100:.1f}%   (正样本 {tp_samples} / 误报 {fp_samples})")

    # 漏检明细
    print("\n  漏检明细（期望命中但未命中）：")
    any_fn = False
    for e in violation:
        scope = guardrails.office62_scope_for_agent({"default_format": e.get("fmt")})
        scan = guardrails.scan_office62_blob(e.get("texts") or [], scope=scope)
        detected = {v["id"] for v in scan["violations"]}
        missed = [x for x in (e.get("expect") or []) if x not in detected]
        if missed:
            any_fn = True
            print(f"    [{e['id']}] 漏检: {', '.join(missed)}")
    if not any_fn:
        print("    （无漏检，全部期望红线均命中）")

    # 合规误报明细
    if benign_fp:
        print("\n  合规误报明细（≥中危，拦截前务必调优）：")
        for e, scan in benign_fp:
            hits = [f"{v['id']}({v['severity']})" for v in scan["violations"]
                    if v["severity"] in ("medium", "high")]
            print(f"    [{e['id']}] fmt={e.get('fmt')} → {', '.join(hits)}")

    print("\n完成。说明：captures.jsonl 可来自两条路径——(1) seeder 生成的真实办公文件字节",)
    print("样本；(2) 线上真实日志：演示后端 OFFICE62_CAPTURE=1 跑一段时间后落盘的产出物文本。",)
    print("二者均无需改代码，本台用「当前规则」重扫即得生产级命中率。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
