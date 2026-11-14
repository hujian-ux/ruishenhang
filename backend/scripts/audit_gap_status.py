#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台缺口台账自动复核脚本（对应 deploy/PLATFORM_GAP_LEDGER_V2_2026-11-14.html）。

把报告中依赖的"事实口径"程序化复验，输出一张状态表，让台账可自动复核、
不再靠人工记忆是否过期。设计为"只读 + 报告"，不修改任何仓库文件。

退出码：
  0 = 无阻断性偏差（仅信息性提示）；
  2 = 发现阻断性偏差（如真源丢失、已接线服务被判定为无引用、孤儿目录重现）。

用法：
  python scripts/audit_gap_status.py            # 控制台表格
  python scripts/audit_gap_status.py --json     # JSON 输出（便于 CI 接）
  python scripts/audit_gap_status.py --root D:/enterprise-ai-platform
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 报告中各项"台账口径"快照（实测日 2026-11-14），用于偏差比对。
LEDGER_STATED = {
    "uncommitted_files": 105,      # A1: 未提交 105 文件（66 ?? + 39 M）
    "services_py_count": 64,       # 报告称 services 目录约 64 个 .py
}

# 状态枚举
OK = "OK"            # 实测与台账一致 / 健康
WARN = "WARN"        # 信息性提示，非阻断（如数字漂移需更新实测日）
STALE = "STALE"      # 口径过时（报告曾误判，现已纠正）
BROKEN = "BROKEN"    # 阻断性偏差


def sh(cwd: Path, cmd: List[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # pragma: no cover
        return 1, str(e)


def count_git_uncommitted(root: Path) -> int:
    rc, out = sh(root, ["git", "status", "--porcelain"])
    if rc != 0:
        return -1
    return len([l for l in out.splitlines() if l.strip()])


def count_uncommitted_by_status(root: Path) -> Tuple[int, int, int]:
    """返回 (新增??, 修改M, 总计)。"""
    rc, out = sh(root, ["git", "status", "--porcelain"])
    if rc != 0:
        return -1, -1, -1
    added = sum(1 for l in out.splitlines() if l.startswith("??"))
    modified = sum(1 for l in out.splitlines() if l.startswith((" M", "M ", "MM", "AM", " M")))
    total = len([l for l in out.splitlines() if l.strip()])
    return added, modified, total


def grep_refs(cwd: Path, pattern: str, path: str = ".") -> int:
    """统计仓库内（排除 .venv）对某个标识符的引用文件数。"""
    rc, out = sh(cwd, ["grep", "-rln", pattern, path, "--include=*.py"])
    if rc != 0:
        return 0
    files = [f for f in out.splitlines() if ".venv" not in f and f.strip()]
    return len(files)


def main() -> int:
    ap = argparse.ArgumentParser(description="平台缺口台账自动复核")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent.parent),
                    help="仓库根目录（默认 backend 的父目录 enterprise-ai-platform）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    backend = root / "backend"
    checks: List[Dict[str, Any]] = []
    broken = False

    # --- 1) 未提交文件数（A1）---
    added, modified, total = count_uncommitted_by_status(root)
    stated = LEDGER_STATED["uncommitted_files"]
    if total < 0:
        checks.append(_row("A1 未提交文件数", "git 不可用", f"台账 {stated}", WARN))
    else:
        status = OK if total == stated else WARN
        note = "" if total == stated else "数字漂移，建议更新实测日/计数"
        checks.append(_row("A1 未提交文件数", f"{total}（??{added}/M{modified}）",
                           f"台账 {stated}", status, note))

    # --- 2) feast_store 是否已接线（C5 纠正项）---
    refs = grep_refs(backend, "feast_store", "app")
    # 排除 app/services/feast_store.py 自身
    rc, out = sh(backend, ["grep", "-rln", "feast_store", "app", "--include=*.py"])
    external = [f for f in out.splitlines() if ".venv" not in f and f != "app/services/feast_store.py"]
    if len(external) >= 1:
        checks.append(_row("C5 feast_store 接线", f"外部引用 {len(external)} 处（已接线）",
                           "台账：已纠正为已接线", OK))
    else:
        checks.append(_row("C5 feast_store 接线", "零外部引用", "台账：已接线", BROKEN,
                           "与台账矛盾，需复查"))
        broken = True

    # --- 3) 孤儿目录是否已清（C6）---
    orphan = backend / "app" / "feast_repo"
    real = backend / "feast_repo"
    if orphan.exists():
        checks.append(_row("C6 孤儿目录 app/feast_repo", "仍存在", "台账：应删除", BROKEN,
                           "孤儿目录重现，确认后删除"))
        broken = True
    else:
        checks.append(_row("C6 孤儿目录 app/feast_repo", "已删除/不存在", "台账：应删除", OK))
    if real.exists() and (real / "feature_store.yaml").exists():
        checks.append(_row("C6 真源 backend/feast_repo", "存在", "台账：真源", OK))
    else:
        checks.append(_row("C6 真源 backend/feast_repo", "缺失", "台账：真源", BROKEN,
                           "真源丢失！REPO_DIR 指向它"))
        broken = True

    # --- 4) feast 包是否安装（Feast 在线服务可用前提）---
    rc, _ = sh(backend, [str(backend / ".venv" / "Scripts" / "python.exe"),
                         "-c", "import feast"])
    if rc == 0:
        checks.append(_row("Feast 包安装", "已安装", "台账：已装 0.68", OK))
    else:
        checks.append(_row("Feast 包安装", "未安装", "台账：已装 0.68", WARN,
                           "未装则在线服务降级，不阻断"))

    # --- 5) 调度器前端引用（B1 缺口项，应为 0）---
    web_src = root / "web" / "src"
    sched_refs = 0
    if web_src.exists():
        rc, out = sh(root, ["grep", "-rln", "scheduler", "web/src"])
        sched_refs = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else 0
    checks.append(_row("B1 调度器前端引用", f"{sched_refs} 处", "台账：0（缺口）",
                       OK if sched_refs == 0 else WARN,
                       "" if sched_refs == 0 else "出现前端引用，缺口状态可能变化"))

    # --- 6) services 目录 .py 数量 ---
    svc_count = len(list((backend / "app" / "services").glob("*.py"))) if (backend / "app" / "services").exists() else -1
    checks.append(_row("services 目录 .py 数", str(svc_count),
                       f"台账约 {LEDGER_STATED['services_py_count']}",
                       OK if svc_count == LEDGER_STATED["services_py_count"] else WARN))

    # --- 7) MLOps 关键函数齐备 ---
    rc, out = sh(backend, [str(backend / ".venv" / "Scripts" / "python.exe"), "-c",
                           "import app.services.mlops as m;"
                           "print(all(hasattr(m,f) for f in "
                           "['run_training_pipeline','rollback_model','feast_sync_dataset',"
                           "'feast_online_features','scheduled_retrain']))"])
    mlops_ok = rc == 0 and out.strip() == "True"
    checks.append(_row("MLOps 关键函数齐备", "齐备" if mlops_ok else "缺失",
                       "台账：闭环已落地", OK if mlops_ok else BROKEN))
    if not mlops_ok:
        broken = True

    # --- 输出 ---
    if args.json:
        print(json.dumps({"broken": broken, "checks": checks},
                         ensure_ascii=False, indent=2))
    else:
        _print_table(checks)
        print(f"\n阻断性偏差: {'有' if broken else '无'}  "
              f"({'退出码 2' if broken else '退出码 0'}）")

    return 2 if broken else 0


def _row(item: str, actual: str, stated: str, status: str, note: str = "") -> Dict[str, Any]:
    return {"item": item, "actual": actual, "stated": stated, "status": status, "note": note}


def _print_table(checks: List[Dict[str, Any]]) -> None:
    cols = [("检查项", 28), ("实测", 22), ("台账口径", 22), ("状态", 8), ("备注", 30)]
    line = "-" * 114
    print(line)
    print("".join(c.ljust(w) for c, w in cols))
    print(line)
    for c in checks:
        print(c["item"].ljust(28) + c["actual"].ljust(22) + c["stated"].ljust(22) +
              c["status"].ljust(8) + (c["note"] or "").ljust(30))
    print(line)


if __name__ == "__main__":
    sys.exit(main())
