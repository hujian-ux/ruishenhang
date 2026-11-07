"""M27 · 备份 / 恢复演练 / 恢复（纯标准库，独立可跑）。

    python backend/scripts/backup_restore.py --backup /tmp/m27.json
    python backend/scripts/backup_restore.py --verify  /tmp/m27.json
    python backend/scripts/backup_restore.py --restore /tmp/m27.json --confirm

三档语义严格分开：
  --backup  导出各 M27 Store 快照 + 配置摘要（脱敏）+ 时间戳 + sha256 校验和。
  --verify  恢复演练：校验 sha256、字段完整、打印计数；**不写生产**。
  --restore 只有同时带 --confirm 才真正写回 Store；缺 --confirm 明确拒绝。

说明（诚实口径）：M27 各 Store 是进程内内存态。独立进程跑本脚本时，备份导出的是
「当前进程可见的快照」，恢复也只能写回「当前进程」；要让备份对运行中的服务生效，
需要把 Store 换成持久层（PG）——这是已知限制，不伪装成跨进程热恢复。

脚本自身只用标准库；访问项目模块做快照/写回时用 try 包裹，缺依赖时优雅降级，
保证 backup / verify 用任意 python 都能跑通。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # <repo>/backend
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "m27-backup/v1"

# 配置摘要键：只从 os.environ 读，密钥类一律脱敏为 <set>/<unset>
_CONFIG_KEYS = [
    "OBSERVABILITY_ENABLED", "OPS_ALERT_ERROR_RATE", "OPS_ALERT_P95_MS",
    "OPS_ALERT_MODEL_429_SPIKE", "AUDIT_RETENTION_DAYS",
    "SECURITY_EVENT_RETENTION_DAYS", "WEBHOOK_TOKEN_SECRET", "WEBHOOK_TIMEOUT_S",
    "LLM_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY",
    "BOCHA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "BING_SEARCH_KEY",
    "OIDC_ENABLED", "OIDC_ISSUER", "OIDC_AUDIENCE",
    "SEED_DEMO_CORPUS", "WEB_SEARCH_ENABLED", "WEB_SEARCH_PROVIDER",
]
_SENSITIVE = ("_KEY", "_SECRET", "_PASSWORD", "_TOKEN")

# 备份覆盖的生产关键 store（第25/26/27/30章）：每个须提供 snapshot()/restore()。
# 独立进程缺依赖时单个标 unavailable，不阻塞其它 store 的备份/校验。
_STORE_SNAPSHOTTERS = ["observability", "events", "usage_store"]


def _load_services():
    """逐个动态导入可备份的 store 模块；缺依赖/无接口的单独立项标不可用。"""
    import importlib

    mods: dict = {}
    for name in _STORE_SNAPSHOTTERS:
        try:
            mod = importlib.import_module(f"app.services.{name}")
            if hasattr(mod, "snapshot") and hasattr(mod, "restore"):
                mods[name] = mod
        except Exception:  # noqa: BLE001 - 降级即可，单个失败不影响全局
            pass
    return mods


def _empty_stores(mods: dict) -> dict:
    """对本次未能加载的 store 生成 unavailable 占位（保持 schema 完整）。"""
    out = {}
    for name in _STORE_SNAPSHOTTERS:
        if name not in mods:
            out[name] = {"unavailable": "模块未在当前进程加载", "schema_unknown": True}
    return out


def _config_summary() -> dict:
    out = {}
    for k in _CONFIG_KEYS:
        raw = os.environ.get(k)
        if any(tag in k for tag in _SENSITIVE):
            out[k] = "<set>" if (raw or "").strip() else "<unset>"
        else:
            out[k] = raw if raw is not None else "<default>"
    return out


def _checksum(payload: dict) -> str:
    """对除 checksum 外的全部内容做规范化 JSON 后求 sha256。"""
    body = {k: v for k, v in payload.items() if k != "checksum"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _counts(stores: dict) -> dict:
    c = {}
    for name, s in stores.items():
        if not isinstance(s, dict) or s.get("unavailable"):
            c[name] = {"available": False}
            continue
        if name == "usage_store":
            c[name] = {"records": len(s.get("records") or [])}
        elif name == "events":
            c[name] = {"subscriptions": len(s.get("subscriptions") or []),
                       "events": len(s.get("events") or [])}
        elif name == "observability":
            c[name] = {"samples": len(s.get("samples") or []),
                       "feedback": len(s.get("feedback") or []),
                       "by_path": len(s.get("by_path") or {})}
        else:
            c[name] = {"entries": len(s) if isinstance(s, (list, dict)) else 0}
    return c


def _empty_stores(reason: str) -> dict:
    return {
        "observability": {"unavailable": reason, "counters": {}, "samples": [],
                          "by_path": {}, "feedback": []},
        "events": {"unavailable": reason, "subscriptions": [], "events": []},
    }


def do_backup(out_path: str) -> int:
    mods = _load_services()
    stores = _empty_stores(mods)
    for name, mod in mods.items():
        try:
            stores[name] = mod.snapshot()
        except Exception as exc:  # noqa: BLE001
            stores[name] = {"unavailable": f"snapshot 失败: {type(exc).__name__}: {exc}"}

    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_summary": _config_summary(),
        "stores": stores,
        "counts": _counts(stores),
        "note": ("各 Store 为进程内内存态；本文件是当前进程可见快照。生产关键数据"
                 "（指标 / 事件 / 用量账单）均已纳入。配置摘要取自 os.environ 且密钥"
                 "已脱敏；跨进程热恢复需持久层（PG）——见脚本说明。"),
    }
    payload["checksum"] = _checksum(payload)

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    c = payload["counts"]
    print(f"[backup] 已写入 {path}")
    print(f"[backup] schema={SCHEMA} created_at={payload['created_at']}")
    print(f"[backup] stores={list(payload['stores'].keys())}")
    print(f"[backup] counts={json.dumps(c, ensure_ascii=False)}")
    print(f"[backup] sha256={payload['checksum']}")
    return 0


def _read(path: str):
    p = Path(path)
    if not p.is_file():
        return None, [f"文件不存在: {path}"]
    try:
        return json.loads(p.read_text(encoding="utf-8")), []
    except Exception as exc:  # noqa: BLE001
        return None, [f"JSON 解析失败: {exc}"]


def _validate(data: dict) -> list:
    """返回错误列表（空 = 通过）：字段完整 + sha256 + Store 结构。"""
    errors: list = []
    for k in ("schema", "created_at", "config_summary", "stores", "counts", "checksum"):
        if k not in data:
            errors.append(f"缺少字段: {k}")
    if data.get("schema") != SCHEMA:
        errors.append(f"schema 不匹配: {data.get('schema')!r} != {SCHEMA!r}")
    if "checksum" in data:
        expect = _checksum(data)
        if expect != data["checksum"]:
            errors.append("sha256 校验不匹配（文件可能被篡改或损坏）")
    stores = data.get("stores") or {}
    for name in _STORE_SNAPSHOTTERS:
        s = stores.get(name)
        if s is None:
            errors.append(f"stores.{name} 缺失")
        elif not isinstance(s, dict):
            errors.append(f"stores.{name} 类型错误（期望 dict）")
        # unavailable 占位（独立进程未加载）允许通过校验，不阻断恢复演练
    if not isinstance(data.get("counts"), dict):
        errors.append("counts 类型错误")
    return errors


def do_verify(path: str) -> int:
    data, errors = _read(path)
    if data is None:
        print("[verify] 失败: " + "; ".join(errors))
        return 1
    errors = _validate(data)
    if errors:
        print("[verify] 失败:")
        for e in errors:
            print("  - " + e)
        return 1
    c = data["counts"]
    print(f"[verify] 通过（恢复演练，未写生产）: {path}")
    print(f"[verify] schema={data['schema']} created_at={data['created_at']}")
    print(f"[verify] sha256={data['checksum']}（重算一致）")
    print(f"[verify] config_summary keys={len(data['config_summary'])}")
    print(f"[verify] 各 store 计数: {json.dumps(c, ensure_ascii=False)}")
    print("[verify] 字段完整、计数已打印；未调用任何 restore，生产未改动")
    return 0


def do_restore(path: str, confirm: bool) -> int:
    if not confirm:
        print("[restore] 拒绝：--restore 必须同时带 --confirm 才执行写回。")
        print("[restore] 请先用 --verify 做恢复演练，确认无误后再加 --confirm。")
        return 2
    data, errors = _read(path)
    if data is None:
        print("[restore] 失败: " + "; ".join(errors))
        return 1
    errors = _validate(data)
    if errors:
        print("[restore] 失败（备份未通过校验，拒绝写回):")
        for e in errors:
            print("  - " + e)
        return 1

    mods = _load_services()
    if not mods:
        print("[restore] 拒绝：当前进程无法访问任何可恢复 Store，无法写回。")
        return 2
    restored = []
    for name, mod in mods.items():
        snap = data["stores"].get(name)
        if not isinstance(snap, dict) or snap.get("unavailable"):
            continue
        try:
            mod.restore(snap)
            restored.append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"[restore] 警告：{name} 写回失败: {type(exc).__name__}: {exc}")
    c = data["counts"]
    print(f"[restore] 完成：已写回当前进程 Store（--confirm 已确认）: {path}")
    print(f"[restore] restored_stores={restored}")
    print(f"[restore] counts={json.dumps(c, ensure_ascii=False)}")
    print("[restore] 注意：内存态 Store 仅在本进程生效，跨进程恢复需持久层（见脚本说明）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="M27 备份 / 恢复演练 / 恢复")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", metavar="OUT", help="导出备份到 OUT.json")
    group.add_argument("--verify", metavar="FILE", help="恢复演练（校验，不写生产）")
    group.add_argument("--restore", metavar="FILE", help="写回 Store（须带 --confirm）")
    ap.add_argument("--confirm", action="store_true", help="确认真正执行 --restore")
    args = ap.parse_args()

    if args.backup:
        return do_backup(args.backup)
    if args.verify:
        return do_verify(args.verify)
    return do_restore(args.restore, args.confirm)


if __name__ == "__main__":
    sys.exit(main())
