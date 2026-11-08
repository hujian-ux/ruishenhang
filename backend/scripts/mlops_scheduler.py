"""M27/P2 · MLOps 自动重训调度入口（真实可跑，非占位）。

把「漂移检测 → 真实训练 → 可选自动晋升」封装为可被子命令 / cron / systemd 调用的
可执行入口。相对旧骨架的「建议重训」钩子，这里是真正跑训练。

用法（在 backend/ 目录下，venv 已激活）：
  .venv/Scripts/python.exe scripts/mlops_scheduler.py --list-datasets
  .venv/Scripts/python.exe scripts/mlops_scheduler.py \
      --baseline <dataset_id> --observed observed.json --auto-promote
  .venv/Scripts/python.exe scripts/mlops_scheduler.py --scan --observed observed.json

observed 支持：
  - .json：记录数组 [{"col":v,...}, ...]
  - .csv：首行表头，其余为记录

安全：默认不自动晋升；显式 --auto-promote 才在指标更优时晋升，避免回退。
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import mlops  # noqa: E402


def _load_observed(path: str):
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    rows = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: (v if v != "" else None) for k, v in r.items()})
    return rows


def main():
    ap = argparse.ArgumentParser(description="MLOps 自动重训调度入口")
    ap.add_argument("--list-datasets", action="store_true", help="列出已登记数据集后退出")
    ap.add_argument("--baseline", help="基线数据集 id（不填则需 --scan）")
    ap.add_argument("--observed", help="observed 记录文件（.json/.csv）")
    ap.add_argument("--scan", action="store_true", help="对全部数据集跑漂移+重训")
    ap.add_argument("--auto-promote", action="store_true", help="指标更优时自动晋升")
    ap.add_argument("--tenant", default="")
    ap.add_argument("--dry-run", action="store_true", help="只报告漂移，不训练")
    args = ap.parse_args()

    observed = _load_observed(args.observed) if args.observed else None

    if args.list_datasets:
        for d in mlops.list_datasets(tenant_id=args.tenant):
            print(f"  {d['id']}  {d.get('name','')}  rows={d.get('n_rows')}  cols={d.get('feature_cols')}")
        return

    targets = []
    if args.baseline:
        targets = [args.baseline]
    elif args.scan:
        targets = [d["id"] for d in mlops.list_datasets(tenant_id=args.tenant)]
    else:
        ap.error("需指定 --baseline <id> 或 --scan")

    if not observed and not args.dry_run:
        ap.error("--observed 为必填（除非 --dry-run 只查漂移）")

    for ds in targets:
        print(f"== 数据集 {ds} ==")
        if args.dry_run:
            rep = mlops.run_drift(args.tenant, ds, observed_records=observed or [])
            print(f"   漂移: overall_drift={rep['overall_drift']} 特征数={rep['feature_count']}")
            continue
        out = mlops.scheduled_retrain(ds, observed or [], tenant_id=args.tenant,
                                      auto_promote=args.auto_promote)
        print("   " + json.dumps(out, ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
