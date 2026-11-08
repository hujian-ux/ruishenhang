# 方案库投放区（166 个 xlsx 交付物 → few-shot）

把 **166 个方案目录** 放到本目录（或任何目录），每个子目录放该方案的「原始数据 ↔ 交付物」成对 xlsx。
`scripts/build_fewshot.py` / `POST /ops/build-fewshot`（平台管理员）会遍历 `SOLUTIONS_ROOT`，
按文件名关键词把 xlsx 配成（输入 ↔ 输出）对，压成紧凑样例块挂到对应业务包；
运行时 `FEWSHOT_ENABLED=1` 把样例拼进技能 system_prompt，提升产出物格式一致性。

## 目录约定
```
corpus_inbox/fewshot_solutions/
├── 方案A/
│   ├── 原始数据.xlsx      # 含「原始/输入/数据/明细/台账/源数据」等关键词之一
│   └── 交付物.xlsx        # 含「交付/输出/成果/报告/结算/报表」等关键词之一
├── 方案B/
│   ├── data.xlsx
│   └── 03_交付报告.xlsx
└── ...（共 166 个）
```
- 无法自动判别时：目录恰好 2 个 xlsx 按（前=输入，后=输出）配对；单文件视为交付物样例。
- 多于 2 个且无法识别则跳过，不强行乱配。

## 启用步骤
1. 设置 `SOLUTIONS_ROOT` 指向方案库根（默认 `D:/workbuddy_解决方案`；本目录可作软链或复制）。
   ```bash
   export SOLUTIONS_ROOT=/path/to/corpus_inbox/fewshot_solutions
   ```
2. 后端 `FEWSHOT_ENABLED=1`（默认关，opt-in，对未启用路径零行为变更）。
3. 触发灌库（管理员）：
   ```bash
   .venv/Scripts/python.exe scripts/build_fewshot.py --base http://127.0.0.1:8080
   ```
   或 `POST /ops/build-fewshot`。返回 `{pairs_found, attached, skipped_no_pair}`。
4. 校验：`GET /agents` 看业务包是否带 `fewshot_version`；抽样比对产出物格式。

## 校验/回滚
- 幂等：重复跑只刷新、不重复叠加。
- 样例块仅作「格式与结构参考，不代表真实数据」，不会把样例数据喂给模型当事实。
- 见 `backend/tests/test_p1_fewshot_enable.py` 的合成端到端验证。
