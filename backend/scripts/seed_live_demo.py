"""把技能包导入「正在运行」的服务，供现场演示/试用手动点。

与 import_solutions.py --apply 的区别：那个直连进程内存（适合脚本/测试），
这个走真实 HTTP 接口（导入 → 批次审批 → 发布），和客户上线是同一条路径，
所以演示看到的状态就是真实流程的状态。

用法（先起后端）：
    .venv/Scripts/python.exe scripts/seed_live_demo.py
    .venv/Scripts/python.exe scripts/seed_live_demo.py --base http://127.0.0.1:8080 --limit 30

只导入前 N 个（--limit）时，批次单更小、审批页更清爽，适合只做体验。
"""
import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_solutions import SOLUTIONS_ROOT, scan  # noqa: E402


def _login(client: httpx.Client, base: str, code: str) -> str:
    r = client.post(f"{base}/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": code,
                          "device_id": "dev", "state": "x"})
    r.raise_for_status()
    return r.json()["data"]["token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--root", default=SOLUTIONS_ROOT)
    ap.add_argument("--limit", type=int, default=0, help="只导入前 N 个（0=全部）")
    ap.add_argument("--skip-approve", action="store_true", help="只导入不发布，留一张待办给演示点")
    a = ap.parse_args()

    defs = scan(a.root)
    if not defs:
        print(f"没在 {a.root} 找到 skill.md")
        return 2
    if a.limit:
        defs = defs[: a.limit]
    print(f"解析到 {len(defs)} 个技能包")

    items = [{
        "name": d["name"],
        "description": d["description"] or d["name"],
        "system_prompt": d["system_prompt"],
        "adapter_type": d["adapter_type"],
        "knowledge_scope": "department",
        "category": d["kind"],
        "category_cn": d["board_cn"],
        "sub_function": d["sub_function"] or None,
        "tags": d["tags"],
        "source_path": d["source"],
        "inputs_contract": d["inputs_contract"],
        "outputs_contract": d["outputs_contract"],
    } for d in defs]

    with httpx.Client(timeout=120) as c:
        token = _login(c, a.base, "dev_admin")
        H = {"Authorization": token}
        r = c.post(f"{a.base}/api/v1/agents/import", headers=H,
                   json={"items": items, "batch_name": "技能包试用导入", "batch_approval": True})
        r.raise_for_status()
        data = r.json()["data"]
        print(f"新建 {data.get('created')} 个，跳过 {len(data.get('skipped') or [])} 个")

        tickets = data.get("approvals") or []
        if not tickets:
            print("没有生成审批单（可能全部已存在）")
            return 0
        aid = tickets[0]["approval_id"]
        print(f"审批单：{aid}（覆盖 {len(tickets[0].get('agent_ids') or [])} 个智能体）")
        if a.skip_approve:
            print("已保留待审状态，去「审批」页点通过即可发布")
            return 0
        d = c.post(f"{a.base}/api/v1/approval/{aid}/decide", headers=H,
                   json={"decision": "approve", "comment": "试用导入，一次放行"})
        d.raise_for_status()
        st = (d.json().get("data") or {}).get("status")
        print(f"审批结果：{st or 'ok'}")

        cat = c.get(f"{a.base}/api/v1/agents/catalog", headers=H).json()["data"]
        print(f"目录现有 {cat.get('total')} 个，分组：")
        for g in cat.get("categories") or []:
            print(f"  - {g.get('category_cn') or g.get('category')}: {g.get('count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
