"""M25 · RAG 答案质量验收（制度查证技能）。

M24 只证明了「接线通」：tool_calls 里有 rag_retrieve、能拿到引用片段。
但当时模型是 [stub:glm-4.6]，答案质量三个问题一个都没回答：
  1. 查不到时会不会老实说没查到，还是拿常识编一段？
  2. 新旧制度冲突时会不会并列，还是自作主张挑一个？
  3. 引用能不能对得上出处，而不是编一个文档名？

这个脚本把语料、问题、判定口径固化下来，等真模型 key 到位一键复跑。

用法（先起后端 uvicorn，再跑）：
    .venv/Scripts/python.exe scripts/verify_rag_answers.py
    .venv/Scripts/python.exe scripts/verify_rag_answers.py --seed-only   # 只灌语料
    .venv/Scripts/python.exe scripts/verify_rag_answers.py --base-url http://127.0.0.1:8080/api/v1

起真模型（充值后）：
    LLM_PROVIDER=direct LLM_VENDOR=siliconflow LLM_API_KEY=<key> \
    LLM_DEFAULT_MODEL=zai-org/GLM-5.3 LLM_STRICT=1 \
    .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080

退出码：
    0  = 语义验收全部通过（**只有真模型下才可能**）
    1  = 真模型下存在 FAIL
    2  = 模型仍是占位，只完成检索层验收，语义项 SKIPPED（不冒充通过）
    3  = 环境/接口错误（服务没起、灌库失败等）

语料在 fixtures/rag_conflicts/，是**验收专用夹具**，不要灌进生产知识库。
三份文档刻意制造了两种冲突：
  · 2023 版与 2026 修订版对同一岗位的限额、超标审批人给出不同数字；
  · 2025 版报销办法仍引用已废止的 2023 版和分管副总审批。
第三问「海外住宿标准」三份文档都没写，用来验「查不到说没查到」——
注意这时检索**仍会有命中**（词高度重合），比零命中更严格：考的是
模型能不能在片段里找不到答案时不用常识补。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "rag_conflicts"
REPORT = ROOT / "reports" / "rag_answer_quality.json"

TITLES = [
    "差旅住宿标准（2023 版）｜验收夹具",
    "差旅住宿标准（2026 修订版）｜验收夹具",
    "费用报销管理办法（2025 版）｜验收夹具",
]


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


# ---------------------------------------------------------------- 验收用例
# 每个 case 分两层：
#   retrieve_check —— 不依赖模型，现在就能验（证据有没有被找回来）
#   semantic_checks —— 只在 llm_real=True 时判，stub 下必须 SKIP 而不是 PASS
CASES = [
    {
        "id": "conflict",
        "name": "新旧冲突是否并列",
        "question": "部门经理去上海出差，住宿费每晚上限是多少？超标了找谁审批？",
        "retrieve": lambda hits: (
            _has_any(" ".join(h.get("title", "") for h in hits), ["2023"])
            and _has_any(" ".join(h.get("title", "") for h in hits), ["2026"])
        ),
        "retrieve_note": "2023 版与 2026 版都要被召回，且多句证据里要含全部冲突因子",
        # 一篇文档只取一句话时，CEO/废止这些关键事实根本进不了 prompt，
        # 真模型再强也答不出冲突并列。所以这里在检索层就卡死证据充分性。
        "evidence_tokens": ["600", "550", "分管副总", "CEO", "废止"],
        "checks": [
            ("两版金额都出现", lambda a: ("600" in a and "550" in a) or ("500" in a and "450" in a)),
            ("标出版本/冲突/待确认",
             lambda a: _has_any(a, ["冲突", "不一致", "待确认", "版本", "废止", "以哪", "需确认", "需核实"])),
            ("两版审批人都出现或明确点出分歧",
             lambda a: ("分管副总" in a and "CEO" in a) or _has_any(a, ["审批人不一致", "审批权限冲突"])),
            ("不替公司拍板",
             lambda a: not _has_any(a, ["已批准", "应当通过", "公司决定按", "最终以此为准"])),
            ("带出处编号或文档名",
             lambda a: bool(re.search(r"\[\d+\]", a)) or "《" in a or "XZ-20" in a),
        ],
    },
    {
        "id": "missing",
        "name": "查不到时是否老实说没查到",
        "question": "员工去海外出差，住宿费每晚上限是多少？超标审批流程是什么？",
        "retrieve": lambda hits: bool(hits),  # 有命中才算严格：片段在眼前但里面没答案
        "retrieve_note": "允许甚至需要有命中（词重合），考的是片段没答案时不许编",
        "checks": [
            ("明确说没查到",
             lambda a: _has_any(a, ["未查到", "没有规定", "未提及", "未规定", "未涉及", "查不到", "无相关", "未说明", "没有找到"])),
            ("不把国内标准当成海外标准",
             lambda a: not _has_any(a, ["海外住宿费上限为", "海外的标准是", "海外为 500", "海外为 550"])),
            ("点明片段只覆盖国内",
             lambda a: _has_any(a, ["国内", "仅适用", "适用范围"])),
        ],
    },
    {
        "id": "citation",
        "name": "有答案时是否带出处",
        "question": "费用报销每月几号截止？依据是哪份文件？",
        "retrieve": lambda hits: _has_any(" ".join(h.get("title", "") for h in hits), ["报销管理办法"]),
        "retrieve_note": "要召回 2025 版报销办法",
        "checks": [
            ("答出 25 日", lambda a: "25" in a),
            ("给出处", lambda a: bool(re.search(r"\[\d+\]", a)) or "《" in a or "CW-2025" in a),
        ],
    },
]


def login(client: httpx.Client, base: str) -> str:
    r = client.post(f"{base}/auth/sso/callback",
                    json={"provider": "dev", "code": "dev_admin", "device_id": "m25-verify"})
    r.raise_for_status()
    return r.json()["data"]["token"]


def seed(client: httpx.Client, base: str, headers: dict) -> list:
    """重建 3 份冲突制度夹具。

    先按 title 清掉旧夹具再灌，而不是靠内容指纹去重：改过一版语料后，
    旧版同名文档会留在库里，检索多召回一篇，冲突用例的结论就不可信了。
    验收要的是**确定性**，不是省几次写入。删除接口会同步清检索语料。
    """
    files = sorted(FIXTURES.glob("*.md"))
    if len(files) != 3:
        raise RuntimeError(f"期望 3 份夹具语料，实际 {len(files)} 份：{FIXTURES}")
    listed = client.get(f"{base}/kb/documents", headers=headers)
    listed.raise_for_status()
    for d in listed.json()["data"]["documents"]:
        if d.get("title") in TITLES:
            client.delete(f"{base}/kb/documents/{d['doc_id']}", headers=headers).raise_for_status()
    out = []
    for path, title in zip(files, TITLES):
        r = client.post(f"{base}/kb/documents", headers=headers, json={
            "title": title,
            "content": path.read_text(encoding="utf-8"),
            "scope": "global",   # 全局：员工身份也要能检索到（M20 踩过部门 scope 的坑）
        })
        r.raise_for_status()
        d = r.json()["data"]
        out.append({"title": title, "doc_id": d["doc_id"], "chars": d.get("chars")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 答案质量验收")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/api/v1")
    ap.add_argument("--seed-only", action="store_true", help="只灌语料，不跑问答")
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()

    try:
        with httpx.Client(timeout=args.timeout) as client:
            token = login(client, args.base_url)
            headers = {"Authorization": token}
            docs = seed(client, args.base_url, headers)
    except Exception as exc:
        print(f"[环境错误] 服务没起 / 登录或灌库失败：{exc}", file=sys.stderr)
        return 3

    print("=" * 78)
    print("语料灌入（scope=global，旧夹具先清后灌，保证确定性）")
    for d in docs:
        print(f"  · {d['title']}  {d['chars']} 字  新建")
    if args.seed_only:
        print("=" * 78)
        return 0

    results, stub_seen = [], False
    print("=" * 78)
    for case in CASES:
        try:
            with httpx.Client(timeout=args.timeout) as client:
                rr = client.post(f"{args.base_url}/rag/retrieve", headers=headers,
                                 json={"query": case["question"], "top_k": 6,
                                       "tenant_id": "t_demo"})
                rr.raise_for_status()
                hits = rr.json()["data"]["hits"]
                iv = client.post(f"{args.base_url}/agent/invoke", headers=headers, json={
                    "agent_id": "ag_pub_kb_qa", "input": case["question"],
                    "stream": False, "output_formats": [],
                })
                iv.raise_for_status()
                data = iv.json()["data"]
        except Exception as exc:
            print(f"[环境错误] {case['id']} 调用失败：{exc}", file=sys.stderr)
            return 3

        answer = data.get("answer") or ""
        real = bool(data.get("llm_real"))
        if not real:
            stub_seen = True
        snips_all = " ".join(
            " ".join(h.get("snippets") or [h.get("snippet") or ""]) for h in hits)
        missing_tokens = [t for t in case.get("evidence_tokens", []) if t not in snips_all]
        retrieval_ok = bool(case["retrieve"](hits)) and not missing_tokens

        print(f"\n[{case['id']}] {case['name']}")
        print(f"  问题：{case['question']}")
        print(f"  检索：{len(hits)} 条命中，证据就位 = {'PASS' if retrieval_ok else 'FAIL'}"
              f"（{case['retrieve_note']}）")
        if missing_tokens:
            print(f"     !! 证据缺因子：{missing_tokens}（模型看不到，语义验收必然失真）")
        for h in hits[:4]:
            sn = " / ".join(h.get("snippets") or [h.get("snippet") or ""])
            print(f"     - {h.get('title')}：{sn.replace(chr(10), ' ')[:150]}")
        print(f"  模型：{'真模型' if real else '占位 stub（语义项不判定）'}"
              f"｜tool_calls={[t.get('tool') for t in data.get('tool_calls') or []]}"
              f"｜引用 {len(data.get('citations') or [])} 条")
        print(f"  答案前 220 字：{answer[:220].replace(chr(10), ' / ')}")

        checks = []
        for label, fn in case["checks"]:
            status = ("SKIP" if not real else ("PASS" if fn(answer) else "FAIL"))
            checks.append({"item": label, "status": status})
            if status != "SKIP":
                print(f"    {status}  {label}")
        if not real:
            print("    SKIP  全部语义项（stub 答案不含推理，判了也没意义）")
        # case 里含 lambda，不落报告，只记 id/name 等可序列化字段
        results.append({"id": case["id"], "name": case["name"],
                        "question": case["question"],
                        "retrieval_ok": retrieval_ok,
                        "llm_real": real, "checks": checks,
                        "evidence_missing": missing_tokens,
                        "answer": answer, "hits": len(hits),
                        "citations": len(data.get("citations") or [])})

    retrieval_all = all(r["retrieval_ok"] for r in results)
    fails = [(r["id"], c["item"]) for r in results for c in r["checks"] if c["status"] == "FAIL"]
    print("\n" + "=" * 78)
    print(f"检索层：{'PASS' if retrieval_all else 'FAIL'}（3/3 用例证据就位）")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"retrieval_all": retrieval_all, "stub_mode": stub_seen,
               "fails": fails, "results": results}
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{REPORT}")

    if stub_seen:
        print("结论：模型仍是占位，**答案质量未验收**，语义项 SKIP（不是 PASS）。")
        print("      充值后按文件头注释起真模型，重跑本脚本即可出结论。")
        return 2
    if fails or not retrieval_all:
        print(f"结论：FAIL {len(fails)} 项 —— {fails}")
        return 1
    print("结论：PASS（真模型下三项语义验收全部通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
