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

_STORE_FIELDS = {
    "observability": ("counters", "samples", "by_path", "feedback"),
    "events": ("subscriptions", "events"),
}


def _load_services():
    """尽量拿到 M27 两个模块；独立 python 缺依赖时返回 (None, 原因)。"""
    try:
        from app.services import events, observability
        return (observability, events), None
    except Exception as exc:  # noqa: BLE001 - 降级即可，不必区分具体缺失
        return None, f"{type(exc).__name__}: {exc}"


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
    obs = stores.get("observability") or {}
    evs = stores.get("events") or {}
    return {
        "observability_counters": obs.get("counters", {}),
        "observability_samples": len(obs.get("samples") or []),
        "observability_by_path": len(obs.get("by_path") or {}),
        "observability_feedback": len(obs.get("feedback") or []),
        "subscriptions": len(evs.get("subscriptions") or []),
        "events": len(evs.get("events") or []),
    }


def _empty_stores(reason: str) -> dict:
    return {
        "observability": {"unavailable": reason, "counters": {}, "samples": [],
                          "by_path": {}, "feedback": []},
        "events": {"unavailable": reason, "subscriptions": [], "events": []},
    }


def do_backup(out_path: str) -> int:
    services, reason = _load_services()
    if services is not None:
        observability, events = services
        stores = {"observability": observability.snapshot(), "events": events.snapshot()}
    else:
        stores = _empty_stores(reason)

    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_summary": _config_summary(),
        "stores": stores,
        "counts": _counts(stores),
        "note": ("各 M27 Store 为进程内内存态；本文件是当前进程可见快照。"
                 "配置摘要取自 os.environ 且密钥已脱敏；生产需持久层做跨进程恢复。"),
    }
    payload["checksum"] = _checksum(payload)

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    c = payload["counts"]
    print(f"[backup] 已写入 {path}")
    print(f"[backup] schema={SCHEMA} created_at={payload['created_at']}")
    print(f"[backup] services={'ok' if services else 'unavailable(' + str(reason) + ')'}")
    print(f"[backup] samples={c['observability_samples']} feedback={c['observability_feedback']} "
          f"subscriptions={c['subscriptions']} events={c['events']}")
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
    for name, fields in _STORE_FIELDS.items():
        s = stores.get(name)
        if not isinstance(s, dict):
            errors.append(f"stores.{name} 缺失或类型错误")
            continue
        for f in fields:
            if f not in s:
                errors.append(f"stores.{name}.{f} 缺失")
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
    print(f"[verify] counters={c.get('observability_counters')}")
    print(f"[verify] samples={c.get('observability_samples')} "
          f"by_path={c.get('observability_by_path')} "
          f"feedback={c.get('observability_feedback')} "
          f"subscriptions={c.get('subscriptions')} events={c.get('events')}")
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
        print("[restore] 失败（备份未通过校验，拒绝写回）:")
        for e in errors:
            print("  - " + e)
        return 1

    services, reason = _load_services()
    if services is None:
        print(f"[restore] 拒绝：当前进程无法访问 M27 Store（{reason}），无法写回。")
        return 2
    observability, events = services
    observability.restore(data["stores"]["observability"])
    events.restore(data["stores"]["events"])
    c = data["counts"]
    print(f"[restore] 完成：已写回当前进程 Store（--confirm 已确认）: {path}")
    print(f"[restore] samples={c.get('observability_samples')} "
          f"feedback={c.get('observability_feedback')} "
          f"subscriptions={c.get('subscriptions')} events={c.get('events')}")
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
