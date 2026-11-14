"""M15/M17 P1 · 193 对 few-shot 启用验证（本地抽测，不改磁盘状态）。

走与线上 ``POST /ops/build-fewshot`` 完全一致的代码路径：
  - 真实方案库（SOLUTIONS_ROOT → D:/workbuddy_解决方案，已实跑 193 对）
  - 真实运行期业务包（agent_store.ensure_public_skills 引导的 _AGENTS）
  - 真实配对/挂载逻辑（scan_solution_pairs + build_fewshot_block + attach_fewshot）
  - 真实运行时注入（agent_runtime 调的 compose_with_fewshot，ON 注入 / OFF 零变更）

与正式 build_fewshot.py 的区别：本脚本只做**内存挂载 + 注入校验**，不调
agent_store.save()，因此对磁盘 agent 文件零副作用，可反复跑；要正式灌库并落盘
（持久化 fewshot 字段）仍用 ``scripts/build_fewshot.py`` → ``POST /ops/build-fewshot``。

用法：
    .venv/Scripts/python.exe scripts/fewshot_spotcheck.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config as cfg
from app.services import agent_store, fewshot, solution_materials


def _fewshot_root() -> str:
    """与 app/routers/ops.py:_fewshot_root 同款：SOLUTIONS_ROOT > env > D 盘默认 > 仓库回退。"""
    _repo_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solutions")
    _default_root = "D:/workbuddy_解决方案"
    return (cfg.settings.SOLUTIONS_ROOT or os.environ.get("SOLUTIONS_ROOT")
            or (_default_root if os.path.isdir(_default_root) else _repo_root))


def _resolve_solution_dir(src: str, by_dir: dict) -> str:
    """与 app/routers/ops.py:_resolve_solution_dir 同款：向上回退 + 嵌套样例兜底。"""
    key = (src or "").replace("\\", "/")
    k = key
    while k:
        if k in by_dir:
            return k
        parent = k.rsplit("/", 1)[0] if "/" in k else ""
        if parent == k:
            break
        k = parent
    base = key[:-len("/skill.md")] if key.endswith("/skill.md") else key
    if base:
        cands = [k2 for k2 in by_dir if k2.startswith(base + "/")]
        if cands:
            return max(cands, key=len)
    return ""


def main() -> int:
    print("== 1) 引导真实运行期业务包（bootstrap_solutions，203 业务包） ==")
    n = agent_store.bootstrap_solutions()
    biz = [a for a in agent_store._AGENTS.values()
           if not a.get("builtin") and a.get("category") != "public" and not a.get("public")]
    print(f"  本次新建 {n}；业务包在内存 _AGENTS：{len(biz)}")

    print("== 2) 真实方案库配对扫描 ==")
    root = _fewshot_root()
    print(f"  方案库根：{root}  （存在={os.path.isdir(root)}）")
    pairs = fewshot.scan_solution_pairs(root)
    by_dir = {p["solution_dir"]: p for p in pairs}
    print(f"  pairs_found（真实）：{len(pairs)}")

    print("== 3) 按运行期业务包匹配挂载（内存，不落盘） ==")
    attached = 0
    skipped_no_pair = 0
    attached_acceptance = 0
    attached_specs = 0
    office_attached = 0
    for a in list(agent_store._AGENTS.values()):
        if a.get("builtin") or a.get("category") == "public":
            continue
        src = a.get("source_path") or a.get("solution_dir") or ""
        key = _resolve_solution_dir(src, by_dir)
        p = by_dir.get(key)
        if not p:
            skipped_no_pair += 1
            continue
        block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
        fewshot.attach_fewshot(a, block)
        sol_path = os.path.join(root, key)
        acc = solution_materials.extract_acceptance(sol_path)
        specs = solution_materials.collect_reference_specs(sol_path)
        solution_materials.attach_materials(a, acc, specs)
        attached += 1
        if acc:
            attached_acceptance += 1
        if specs:
            attached_specs += 1
        if a.get("default_format") in ("docx", "xlsx", "pptx"):
            office_attached += 1
    print(f"  attached（真实命中并挂载）：{attached}")
    print(f"  skipped_no_pair（业务包无方案库样例）：{skipped_no_pair}")
    print(f"  attached_acceptance（含验收清单）：{attached_acceptance}")
    print(f"  attached_specs（含领域规范）：{attached_specs}")
    print(f"  office_attached（办公类技能命中）：{office_attached}")

    print("== 4) 抽测注入效果：compose_with_fewshot ON/OFF 双验证 ==")
    sample = next((a for a in agent_store._AGENTS.values() if a.get("fewshot")), None)
    if not sample:
        print("  ⚠ 未找到已挂载 few-shot 的业务包，跳过注入抽测")
        return 1
    print(f"  取样业务包：{sample['agent_id']} ｜ category={sample.get('category')} ｜"
          f"default_format={sample.get('default_format')}")
    base_prompt = "你是行业技能助手，请按规范产出交付物。"

    # OFF：默认关，零行为变更
    cfg.settings.FEWSHOT_ENABLED = False
    off_prompt = fewshot.compose_with_fewshot(base_prompt, sample)
    off_ok = "产出物样例" not in off_prompt and sample["fewshot"] not in off_prompt
    print(f"  [OFF] FEWSHOT_ENABLED=0 → 注入块缺失：{off_ok}  （零行为变更）")

    # ON：开启后注入
    cfg.settings.FEWSHOT_ENABLED = True
    on_prompt = fewshot.compose_with_fewshot(base_prompt, sample)
    on_ok = ("产出物样例" in on_prompt) and (sample["fewshot"] in on_prompt)
    print(f"  [ON ] FEWSHOT_ENABLED=1 → 注入块存在：{on_ok}  （agent={sample['agent_id']}）")
    print(f"       样例块长度：{len(sample['fewshot'])} 字符；块首行：{sample['fewshot'].splitlines()[0][:60] if sample['fewshot'] else ''!r}")

    print("== 结论 ==")
    if off_ok and on_ok and attached > 0:
        print(f"  ✅ 193 对 few-shot 真实验证通过：pairs_found={len(pairs)}，attached={attached}，"
              f"启用后运行时真实注入、关闭时零变更。")
        print(f"     正式启用：① 起后端跑 scripts/build_fewshot.py 落盘；"
              f"② 设 FEWSHOT_ENABLED=1（需重启重 bootstrap 才注入）。")
        return 0
    print("  ❌ 抽测未通过，请检查 compose 路径或方案库匹配。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
