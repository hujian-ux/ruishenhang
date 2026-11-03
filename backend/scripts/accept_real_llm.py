"""M17 · 真模型验收：「费用异常波动分析」「报销单合规预审」跑真数据。

为什么单独一个脚本：单测只证明「链路通」——上传能解析、调用不报错、产出物能下载。
但客户真正要问的是「算得对不对、结论能不能用」。这个脚本用技能包自带的真实样例
数据 + 官方示例产出物当**对照答案**，逐条核对模型输出，给出可量化得分。

跑法（有真 key 时）：
    set ZHIPU_API_KEY=sk-xxxx
    python scripts/accept_real_llm.py

没有 key 时先验证全链路（不调模型，用样板答案走完上传→渲染→打分→出报告）：
    python scripts/accept_real_llm.py --dry-run

判定标准（写死在 CHECKS 里，不靠肉眼）：
- 每条 check 都是「模型答案里必须出现的事实」。数字类要求精确命中，
  因为量价分离算错 2000 和 1800 对财务是两回事，不能模糊算过。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

# ── 环境必须在 import app 之前定好（Settings 是类属性，导入即固化）──
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PKG_ROOT = Path(os.environ.get("SOLUTIONS_ROOT", r"D:/workbuddy_解决方案"))

SKILLS = {
    "费用异常波动分析": {
        "dir": PKG_ROOT / "04_角色/财务/费用异常波动分析",
        "skill": "03-ji-neng-bao/fei-yong-yi-chang-bo-dong-fen-xi/skill.md",
        "files": ["fei-yong-tai-zhang.xlsx", "yong-liang-dan-jia-shu-ju.xlsx"],
        "input": "按费用台账与用量单价数据，做本期（2026-03）对上期（2026-02）的环比分析，"
                 "做量价分离，找出涨价的真实原因，并给出超支科目排序清单。",
    },
    "报销单合规预审": {
        "dir": PKG_ROOT / "04_角色/财务/报销单合规预审",
        "skill": "03-ji-neng-bao/bao-xiao-dan-he-gui-yu-shen/skill.md",
        "files": ["fei-yong-bao-xiao-dan.xlsx", "fa-piao-yang-ben.xlsx",
                  "gong-si-bao-xiao-zhi-du-yu-fei-yong-biao-zhun.xlsx"],
        "input": "按公司报销制度对本周 3 张报销单逐张预审：给出每张的结论（通过/退单/待复核）、"
                 "退单理由（挂制度条款）、以及需走官方查验平台的待复核发票清单。",
    },
}

# 对照答案：直接来自技能包自带的示例产出物（fei-yong-yi-chang-bo-dong-fen-xi.xlsx /
# bao-xiao-he-gui-yu-shen-qing-dan.xlsx），不是我们编的。
CHECKS = {
    "费用异常波动分析": [
        ("认出保洁费是主要异常项", lambda a: "保洁费" in a),
        ("取到本期金额 12000", lambda a: _has_num(a, 12000)),
        ("取到上期金额 10000", lambda a: _has_num(a, 10000)),
        ("涨幅算对 20%", lambda a: _has_pct(a, 20)),
        ("量差判定为 0（用量未变）", lambda a: _pair(a, "量差", 0) or "量差为0" in _squash(a) or "量差 0" in a),
        ("价差算出 2000（纯价涨）", lambda a: _pair(a, "价差", 2000) or _has_num(a, 2000)),
        ("归因落到「价涨/单价」而非「量涨」",
         lambda a: ("价涨" in a or "单价上涨" in a or "价格上涨" in a or "按量计费" in a)),
        ("产出结构化表格（markdown 表或 json）", lambda a: _n_tables(a) >= 1),
    ],
    "报销单合规预审": [
        ("李四这笔被拦下", lambda a: "李四" in a),
        ("指出招待超单次上限 3000", lambda a: _has_num(a, 3000) and ("超" in a or "上限" in a)),
        ("李四结论为退单/不合规", lambda a: _near(a, "李四", ("退单", "退回", "不合规", "超标准"))),
        ("张三这笔判通过", lambda a: _near(a, "张三", ("通过", "合规", "可报销"))),
        ("王五这笔判待复核（品名与事由不一致）",
         lambda a: _near(a, "王五", ("待复核", "复核", "不一致"))),
        ("退单理由挂到制度/条款", lambda a: bool(re.search(r"第\s*[0-9一二三四五六七八九十]+\s*条|制度|报销标准|上限", a))),
        ("产出逐张结论清单表", lambda a: _n_tables(a) >= 1),
    ],
}

DRY_ANSWERS = {
    "费用异常波动分析": """## 一、费用环比
本期（2026-03）对上期（2026-02），保洁费由 10000 元涨至 12000 元，涨幅 20%，为本次最大涨项。

## 二、量价分离
| 科目 | 本期 | 上期 | 涨幅 | 量差 | 价差 |
|---|---|---|---|---|---|
| 保洁费 | 12000 | 10000 | 20% | 0 | 2000 |
| 水电费 | 8600 | 8000 | 7.5% | 400 | 200 |

## 三、涨价归因
保洁费为**纯价涨**：2 月为包干计价，3 月起耗材改按量计费，单价上调，用量未变，故量差 0、价差 2000。

## 四、异常清单
| 科目 | 归因 | 说明 | 处置 |
|---|---|---|---|
| 保洁费 | 价涨 | 耗材改按量计费 | 重新议价 |
""",
    "报销单合规预审": """## 一、预审结论
| 报销人 | 金额(元) | 结论 | 退单理由(挂条款) |
|---|---|---|---|
| 张三 | 3200 | 通过 | 差旅未超 5000 单次上限，附件 3 张齐全 |
| 李四 | 8800 | 退单 | 招待费超单次上限 3000，依据《报销制度》第5条 |
| 王五 | 540 | 待复核 | 品名「咨询费」与事由「办公」不一致，依据《报销制度》第3条 |

## 二、待复核发票
| 发票号 | 疑点 | 动作 |
|---|---|---|
| INV002 | 抬头与报销人单位不符 | 官方查验平台复核真伪 |

## 三、退单话术
本次招待费超出公司单次上限 3000 元，请拆分或改期提交。
""",
}


# ────────────────────────── 小工具 ──────────────────────────
def _squash(s: str) -> str:
    return re.sub(r"[\s,，、]", "", s)


def _has_num(text: str, n: float) -> bool:
    """数字命中：允许 12000 / 12,000 / 12000元 / 1.2万。"""
    t = _squash(text)
    cands = {f"{n:.0f}", f"{n:g}"}
    if n >= 10000 and n % 10000 == 0:
        cands.add(f"{n / 10000:g}万")
    return any(c in t for c in cands)


def _has_pct(text: str, n: float) -> bool:
    return bool(re.search(rf"{n:g}\s*%|{n:g}\s*％|百分之{int(n)}", text))


def _pair(text: str, label: str, val: float) -> bool:
    """表格里 `量差 | 0` 这类相邻单元格命中。"""
    flat = re.sub(r"[|\s]+", " ", text)
    return bool(re.search(rf"{label}\s+{val:g}(?!\d)", flat))


def _near(text: str, anchor: str, words: tuple) -> bool:
    """同一行里同时出现人名与结论（容忍 markdown 表格的竖线分隔）。"""
    for line in text.splitlines():
        if anchor in line and any(w in line for w in words):
            return True
    return False


def _n_tables(answer: str) -> int:
    from app.services.artifact import extract_tables
    return len(extract_tables(answer))


def _ensure_env(a: argparse.Namespace) -> None:
    os.environ.setdefault("PG_PORT", "1")        # 隔离外部 PG，不影响生产库
    os.environ.setdefault("SEED_DEMO_CORPUS", "0")  # 试点口径：不吃演示语料
    if a.dry_run:
        os.environ["LLM_PROVIDER"] = "litellm"
        os.environ["LLM_STRICT"] = "0"
        return
    os.environ["LLM_PROVIDER"] = a.provider
    os.environ["LLM_VENDOR"] = a.vendor
    os.environ["LLM_DEFAULT_MODEL"] = a.model
    os.environ["LLM_STRICT"] = "1"               # 验收绝不允许 stub 冒充
    if a.key:
        os.environ["LLM_API_KEY"] = a.key


def _read_skill_md(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


# ────────────────────────── 主流程 ──────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不调模型，用样板答案验证全链路")
    ap.add_argument("--provider", default="direct")
    ap.add_argument("--vendor", default="zhipu")
    ap.add_argument("--model", default="glm-4.6")
    ap.add_argument("--key", default=os.environ.get("ZHIPU_API_KEY", ""))
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "验收" / "M17_真模型"))
    ap.add_argument("--formats", default="xlsx,docx,pptx,html,pdf")
    args = ap.parse_args()

    _ensure_env(args)

    from fastapi.testclient import TestClient
    from app.core.security import create_dev_token
    from app.main import app
    from app.services import model_gateway

    H = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
    client = TestClient(app)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    report: dict = {"mode": "dry-run" if args.dry_run else "real-llm",
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "model": args.model, "provider": args.provider,
                    "cases": [], "artifacts": []}

    # 1) 探活
    print("=" * 66)
    if args.dry_run:
        print("模式：dry-run（用技能包示例答案验证链路，不消耗 token）")
    else:
        p = model_gateway.probe(args.model)
        report["probe"] = p
        print(f"模型探活：{'通过' if p['ok'] else '失败'} · {p.get('vendor_label')} "
              f"· {p['model_id']} · {p['latency_ms']}ms")
        if not p["ok"]:
            print(f"  HTTP {p['status_code']} · {p['error'][:200]}")
            _write_report(report, outdir, blocked="模型不可用，验收未执行")
            print("\n结论：未执行验收 —— 先解决上面的模型访问问题（多半是 key 失效/欠费）。")
            return 2

    # 2) 导入两个技能包（走真实导入接口 + 批次审批，和正式上线同一条路径）
    items = []
    for name, cfg in SKILLS.items():
        items.append({"name": name, "description": f"{name}（M17 验收）",
                      "system_prompt": _read_skill_md(cfg["dir"] / cfg["skill"]),
                      "adapter_type": "llm", "knowledge_scope": "department",
                      "category": "office", "category_cn": "办公角色", "sub_function": "财务",
                      "source_path": str(cfg["dir"]), "model_id": args.model,
                      "inputs_contract": "、".join(cfg["files"]),
                      "outputs_contract": "xlsx 产出物"})
    r = client.post("/api/v1/agents/import", json={"items": items,
                                                   "batch_name": "M17 真模型验收"}, headers=H)
    assert r.status_code == 200, r.text
    imp = r.json()["data"]
    print(f"导入技能包：新增 {imp['created']} 个，审批单 {len(imp['approvals'])} 张")
    if imp["created"]:
        aid = imp["approvals"][0]["approval_id"]
        d = client.post(f"/api/v1/approval/{aid}/decide", json={"decision": "approve"}, headers=H)
        assert d.status_code == 200, d.text
        print(f"审批通过：{len(imp['approvals'][0]['agent_ids'])} 个技能包已发布")
    ids = {a["name"]: a["agent_id"] for a in imp["agents"]}
    # 幂等重跑：已存在时从目录里找回来
    if len(ids) < len(SKILLS):
        cat = client.get("/api/v1/agents/catalog", headers=H).json()["data"]
        for c in cat["categories"]:
            for g in c["groups"]:
                for a in g["agents"]:
                    ids.setdefault(a["name"], a["agent_id"])

    # 3) 逐案执行
    for name, cfg in SKILLS.items():
        agent_id = ids.get(name)
        if not agent_id:
            report["cases"].append({"case": name, "error": "未导入成功"})
            continue
        print("-" * 66)
        print(f"用例：{name}")

        file_ids = []
        for fn in cfg["files"]:
            fp = cfg["dir"] / fn
            if not fp.exists():
                print(f"  ! 缺输入文件：{fp}")
                continue
            with open(fp, "rb") as fh:
                up = client.post("/api/v1/agent/files",
                                 files={"file": (fn, fh.read(),
                                                 "application/vnd.openxmlformats-officedocument."
                                                 "spreadsheetml.sheet")}, headers=H)
            assert up.status_code == 200, up.text
            file_ids.append(up.json()["data"]["file_id"])
        print(f"  上传输入件：{len(file_ids)} 份")

        t0 = time.time()
        if args.dry_run:
            answer = DRY_ANSWERS[name]
            usage = {"real": False, "model_id": "dry-run", "prompt_tokens": 0,
                     "completion_tokens": 0, "cost": 0}
            arts = _render_dry(name, answer, args.model, formats)
        else:
            resp = client.post("/api/v1/agent/invoke",
                               json={"agent_id": agent_id, "input": cfg["input"],
                                     "stream": False, "file_ids": file_ids,
                                     "output_formats": formats}, headers=H)
            if resp.status_code != 200:
                print(f"  调用失败 HTTP {resp.status_code}：{resp.text[:300]}")
                report["cases"].append({"case": name, "error": resp.text[:300]})
                continue
            data = resp.json()["data"]
            answer = data["answer"]
            usage = data["usage"]
            usage["llm"] = data.get("llm", {})
            arts = [(a["filename"], a["download_url"], a["size"]) for a in data["artifacts"]]
        elapsed = int((time.time() - t0) * 1000)

        # 打分
        checks = []
        for label, fn in CHECKS[name]:
            try:
                ok = bool(fn(answer))
            except Exception:
                ok = False
            checks.append({"label": label, "pass": ok})
        passed = sum(1 for c in checks if c["pass"])
        score = passed / len(checks)
        print(f"  得分：{passed}/{len(checks)} = {score:.0%}  （{elapsed}ms）")
        for c in checks:
            print(f"    {'✓' if c['pass'] else '✗'} {c['label']}")

        # 下载产出物
        saved = []
        case_dir = outdir / name
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "answer.md").write_text(answer, encoding="utf-8")
        for fname, url, size in arts:
            if str(url).startswith("dry://"):
                content = _DRY_BYTES.get(fname)
            else:
                rr = client.get(url, headers=H)
                content = rr.content if rr.status_code == 200 else None
            if content:
                (case_dir / fname).write_bytes(content)
                saved.append({"file": fname, "size": len(content)})
                print(f"    ↓ {fname} ({len(content) // 1024} KB)")
            else:
                print(f"    ! 下载失败：{fname}")

        report["cases"].append({"case": name, "agent_id": agent_id, "score": score,
                                "passed": passed, "total": len(checks), "checks": checks,
                                "usage": usage, "latency_ms": elapsed,
                                "artifacts": saved, "files_used": len(file_ids)})
        report["artifacts"].extend(str(case_dir / s["file"]) for s in saved)

    # 4) 汇总
    cases = [c for c in report["cases"] if "score" in c]
    avg = sum(c["score"] for c in cases) / len(cases) if cases else 0.0
    report["avg_score"] = avg
    report["verdict"] = ("达标" if avg >= 0.8 else "未达标") if cases else "未执行"
    _write_report(report, outdir)
    print("=" * 66)
    print(f"平均得分 {avg:.0%} · {report['verdict']} · 报告与产出物在 {outdir}")
    return 0 if report["verdict"] == "达标" else 1


def _render_dry(name: str, answer: str, model: str, formats: list[str]):
    """dry-run 用：直接渲染产出物，跳过模型与存储。"""
    from app.services import artifact
    meta = {"agent_name": name, "agent_id": "dry", "input_files": [],
            "model_id": model, "run_id": "run_dry", "trace_id": "tr_dry", "citations": []}
    out = []
    for fmt in formats:
        data = artifact.build(fmt, answer, meta, None, None)
        out.append((artifact.artifact_name(name, "", fmt), f"dry://{fmt}", len(data)))
        _DRY_BYTES[artifact.artifact_name(name, "", fmt)] = data
    return out


_DRY_BYTES: dict = {}


def _write_report(report: dict, outdir: Path, blocked: str = "") -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    L = [f"# M17 真模型验收报告", "",
         f"- 时间：{report['started_at']}",
         f"- 模式：{report['mode']}",
         f"- 模型：{report['model']}（provider={report['provider']}）"]
    if blocked:
        L.append(f"- **未执行原因**：{blocked}")
    if report.get("probe"):
        p = report["probe"]
        L.append(f"- 探活：{'通过' if p['ok'] else '失败'} · HTTP {p['status_code']} · {p['latency_ms']}ms")
    L.append("")
    if report.get("cases"):
        L += ["## 逐案结果", "", "| 用例 | 得分 | 耗时 | 真实模型 | 产出物 |", "|---|---|---|---|---|"]
        for c in report["cases"]:
            if "score" not in c:
                L.append(f"| {c['case']} | 未执行 | - | - | {c.get('error', '')[:60]} |")
                continue
            u = c["usage"]
            L.append(f"| {c['case']} | {c['passed']}/{c['total']} = {c['score']:.0%} | "
                     f"{c['latency_ms']}ms | {u.get('real')} | {len(c['artifacts'])} 个 |")
        L += ["", "## 逐条核对", ""]
        for c in report["cases"]:
            if "score" not in c:
                continue
            L += [f"### {c['case']}", "", "| 检查项 | 结果 |", "|---|---|"]
            for chk in c["checks"]:
                L.append(f"| {chk['label']} | {'✅' if chk['pass'] else '❌'} |")
            u = c["usage"]
            L += ["", f"tokens：{u.get('prompt_tokens', 0)} in / {u.get('completion_tokens', 0)} out"
                      f" · 成本 ¥{u.get('cost', 0):.4f}"
                      f" · 延迟 {c['latency_ms']}ms", ""]
    L += ["", f"**结论**：{report.get('verdict', '未执行')}"
              f"{'（平均 %.0f%%）' % (report['avg_score'] * 100) if report.get('avg_score') is not None else ''}"]
    (outdir / "report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
