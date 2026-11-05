"""M26 · 联网搜索通道端到端验收（可重复跑）。

跑法（先起后端 + 灌 115 包）：
    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
    .venv/Scripts/python.exe scripts/seed_live_demo.py
    .venv/Scripts/python.exe scripts/verify_web_search.py

分两层结论，不许混：
- 检索层：通道通不通、来源标签对不对、内外引用有没有分家、产出物出没出；
- 语义层：模型有没有照着「逐条标 [W编号]、查不到说没查到」写。
stub 模式下语义层明确 SKIP，退出码 2，不冒充通过。真模型到位后重跑同一条命令。

退出码：0 全通过；1 检索层失败；2 语义层未验（stub 或通道不可用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "reports" / "web_search_quality.json"

CASES = [
    {"id": "web_lookup", "agent_id": "ag_pub_web_lookup",
     "input": "查一下广东省 2026 年独立储能电站参与现货市场和容量补偿的政策依据，要能点开链接复核",
     "formats": ["docx"], "need": "web_search"},
    {"id": "competitor", "agent_id": "ag_pub_competitor_scan",
     "input": "调研国内面向中小餐饮的半成品菜供应链品牌，比较产品、价格口径、渠道和主打卖点",
     "formats": ["xlsx"], "need": "web_search"},
    {"id": "cross_check", "agent_id": "ag_pub_cross_check",
     "input": "核对公司差旅住宿标准与公开可查的差旅管理惯例有没有冲突，分别列内部依据和公开信息",
     "formats": ["docx"], "need": "both"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/api/v1")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(timeout=120) as c:
        login = c.post(f"{base}/auth/sso/callback",
                       json={"provider": "dev", "code": "dev_admin", "device_id": "verify"})
        login.raise_for_status()
        h = {"Authorization": login.json()["data"]["token"]}

        status = c.get(f"{base}/search/status", headers=h).json()["data"]
        print(f"通道：enabled={status['enabled']} provider={status['provider']} "
              f"mode={status['mode']} allow_fetch={status['allow_fetch']}")
        if not status["enabled"]:
            print("联网通道被配置关闭（WEB_SEARCH_ENABLED=0），无法验收")
            return 1

        probes = []
        for q in ("广东 独立储能 容量补偿 政策 2026", "半成品菜 供应链 品牌 报价"):
            r = c.post(f"{base}/search/web", headers=h, json={"query": q, "top_k": 5})
            r.raise_for_status()
            d = r.json()["data"]
            ok = (d["ok"] and len(d["hits"]) > 0
                  and all(x.get("origin") == "public_web" and x.get("url", "").startswith("http")
                          for x in d["hits"]))
            probes.append({"query": q, "ok": ok, "provider": d["provider"],
                           "mode": d["mode"], "degraded": d["degraded"],
                           "hits": len(d["hits"]), "elapsed_ms": d["elapsed_ms"],
                           "titles": [x["title"] for x in d["hits"][:3]],
                           "error": d["error"]})
            print(f"检索「{q[:22]}…」：{len(d['hits'])} 条 / {d['mode']} / "
                  f"{d['elapsed_ms']}ms / {'PASS' if ok else 'FAIL'}")
        retrieval_all = all(p["ok"] for p in probes)

        results, any_stub = [], False
        for case in CASES:
            r = c.post(f"{base}/agent/invoke", headers=h, json={
                "agent_id": case["agent_id"], "input": case["input"],
                "stream": False, "output_formats": case["formats"]})
            if r.status_code != 200:
                print(f"[{case['id']}] 调用失败 {r.status_code}: {r.text[:200]}")
                results.append({"id": case["id"], "error": r.text[:300], "ok": False})
                continue
            d = r.json()["data"]
            tools = {t["tool"] for t in d.get("tool_calls") or []}
            src = d.get("web_sources") or []
            arts = [a.get("meta", {}).get("format") for a in d.get("artifacts") or []]
            ok = ("web_search" in tools and len(src) > 0 and bool(arts)
                  and all(s.get("origin") == "public_web" for s in src))
            if case["need"] == "both":
                ok = ok and "rag_retrieve" in tools
            real = bool((d.get("llm") or {}).get("real"))
            any_stub = any_stub or not real
            results.append({"id": case["id"], "agent_id": case["agent_id"], "ok": ok,
                            "tools": sorted(tools), "web_sources": len(src),
                            "citations": len(d.get("citations") or []),
                            "artifacts": arts, "llm_real": real,
                            "web_search": d.get("web_search"),
                            "source_titles": [s.get("title") for s in src[:3]],
                            "answer_chars": len(d.get("answer") or "")})
            print(f"[{case['id']}] tools={sorted(tools)} 来源={len(src)} "
                  f"内部引用={len(d.get('citations') or [])} 产出={arts} "
                  f"{'PASS' if ok else 'FAIL'}"
                  + ("" if real else "（模型 stub，语义未验）"))

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status, "probes": probes, "cases": results,
        "retrieval_all": retrieval_all and all(x.get("ok") for x in results),
        "stub_mode": any_stub,
        "semantic_verdict": "SKIP: 模型仍为占位，未验证答案是否逐条标 [W编号]、查不到是否说没查到"
                            if any_stub else "PENDING_MANUAL_REVIEW: 需人工核对来源链接与结论",
        "scope": "公网检索结果与内部 RAG 片段分通道回传；模型为 stub 时只证明链路，不证明答案质量",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n检索层：{'PASS' if report['retrieval_all'] else 'FAIL'}；"
          f"语义层：{report['semantic_verdict']}\n报告：{out}")
    if not report["retrieval_all"]:
        return 1
    return 2 if any_stub else 0


if __name__ == "__main__":
    sys.exit(main())
