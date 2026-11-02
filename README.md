# 企业 AI 智能体中台 · 工程脚手架（M0）

> 与「立项评审包 v1.15 / 蓝图 v1.15」同源。本目录是评审通过后的**工程化落地第一步**：
> 先搭可运行底座（脚手架），再按 P0 三章（第35/36/37章）跑第一迭代。

## 已拍板的技术决策点
| 项 | 决策 | 落点 |
|----|------|------|
| agent-runtime 主框架 | **LangGraph**（可选依赖，未装时退化为确定性 stub） | `backend/app/services/agent_runtime.py` |
| 前端框架 | **React + Vite + TS** | `web/` |
| 部署形态 | **先单体后拆容器**：后端单体直接运行，基础设施用 docker-compose；拆容器时按 `services/` 边界迁移，契约不变 | `docker-compose.yml`（仅基础设施） |
| 中文 Embedding | **bge（bge-m3，维度 1024）** | `settings.EMBEDDING_DIM` / `knowledge.embed()` |

## 目录结构
```
enterprise-ai-platform/
├─ docker-compose.yml        # 仅基础设施：postgres / qdrant / minio / redis / litellm
├─ .env.example              # 后端环境变量（复制为 .env）
├─ Makefile / scripts/        # 本地一键起停（dev.sh / dev.ps1）
├─ backend/                   # ── 后端单体（FastAPI） ──
│  ├─ app/
│  │  ├─ core/                # config / security(X-Caller-Context) / filter(第18章单一过滤点) / middleware(网关)
│  │  ├─ models/              # caller 上下文 + 与 openapi.yaml 对齐的 schemas
│  │  ├─ routers/             # auth/rag/agents/kb/models/usage/approval（openapi 8 paths）
│  │  ├─ services/            # knowledge(bge) / agent_runtime(LangGraph) / model_gateway(LiteLLM) / eval
│  │  └─ selection/           # 第35章 选型决策框架（与原型向导同源）
│  ├─ tests/test_smoke.py     # 6 项 smoke（含过滤点/红线/越权写）
│  └─ Dockerfile              # 后拆容器用（默认不启用）
├─ web/                       # ── React 前端（参考 WorkBuddy 风格） ──
│  └─ src/                    # api/client + App + pages/Agents（选型→建智能体闭环）
├─ infra/init/01_acl.sql      # 权限元数据 / 业务基表（自动初始化）
└─ ci/quality-gate.yml        # 第36章 Recall@100≥0.95 + 第37章 客观评测门禁
```

## 本地运行
```bash
# 1) 基础设施
docker compose up -d
# 2) 后端（默认 :8080，自动打开 /docs）
cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8080
# 3) 前端（默认 :5173，已代理 /api 到 8080）
cd web && npm install && npm run dev
```
也可一键：`make up` 或 `bash scripts/dev.sh`（Windows：`powershell scripts/dev.ps1`）。

> 注：`web/vite.config.ts` 已显式将 dev server 绑定 `host: 127.0.0.1`，避免某些环境 `localhost` 解析到 IPv6(`::1`) 导致本机/代理连不上。前端联调契约脚本：`node web/scripts/contract_check.mjs`（需后端先起）。

## CI 与 Git（已接入 GitHub Actions）
- 本仓库已 `git init` 并完成初始提交；`.github/workflows/quality-gate.yml` 即 CI 门禁，触发条件：`push` 到 `main` / 任意 `pull_request`。
- 流水线三道关：`backend-build`（pytest 全量含最小闭环 e2e）→ `rag-recall-gate`（Recall@100≥0.95）→ `ml-objective-gate`（第37章客观指标，不依赖 LLM-as-judge）。任一不达标非零退出即阻断合并（第20章演化联动）。
- 冗余备份：`ci/quality-gate.yml`（同逻辑，可对接 GitLab CI 等其他平台）。
- **触发方式**：把本仓库 `git remote add origin <你的GitHub地址>` 后 `git push -u origin main`，GitHub 即自动跑该工作流（需先在仓库 Secrets/环境装好 Python 3.11，无需其他配置）。

## 与蓝图的对应（契约同源）
- **openapi.yaml（8 paths / 22 schemas）** ←→ `backend/app/routers/*` 一一映射
- **第18章 单一过滤点** ←→ `core/filter.py` + `core/middleware.py`（只在网关一处）
- **第25章 模型网关** ←→ `services/model_gateway.py` + `litellm_config.yaml`
- **第35章 选型** ←→ `selection/framework.py`（与原型「选型向导」结果一致，导出 JSON 对齐 `/agents`）
- **第36章 RAG / 第37章 评测** ←→ `services/knowledge.py` + `services/eval.py`（含 CI 门禁骨架）
- **多人知识库（scope/owner/dept）** ←→ `infra/init/01_acl.sql` + `core/filter.py`

## 里程碑（脚手架 → P0 → 最小闭环）
| 阶段 | 工期 | 产出 | 验收（门禁） |
|------|------|------|------|
| M0 脚手架 | 2 周 | docker-compose + 单体 + 过滤点 stub + SQL + CI | 本地一键起；`/rag` 返回过滤后结果；CI 可跑 ✅ |
| M1 第35章 选型 | 1 周 | 选型服务化（`/selection/recommend` 端点，与原型向导同源）+ agents 注册校验（缺选型红线 400 / 非法 adapter / 非法 scope 拒绝 / registered_by 服务端回填） | ✅ 选型四分支覆盖；注册成功落库 pending_review；越权角色 403 |
| M2 第36章 RAG | 4 周 | 混合检索(BM25+向量+RRF) + 父子切分 + 重排(启发式降级) + golden 评测集(252条) | ✅ Recall@100=1.0 / filter_accuracy=1.0；质量层与合规层解耦 |
| M3 第37章 评测 | 2 周 | 客观指标(recall/mrr/ndcg/filter_accuracy) + CI 门禁(`ci/quality-gate.yml` 接入 eval) + 演化阻断 | ✅ Recall@100=1.0/MRR@10=0.754/NDCG@10=0.811/filter_accuracy=1.0；门禁不达标非零退出阻断 |
| 最小闭环 | — | 选型→建智能体→提问→过滤→评测 端到端 | ✅ `tests/test_e2e_loop.py` 全链路通过（`scripts/e2e_walkthrough.py` 人读走查报告） |
| M4 编排与记忆 | 2 周 | `/agent/invoke` 多轮编排（LangGraph 记忆接入：query 改写+上下文注入）+ 会话记忆（归属校验/滑动窗口）+ `/agent/sessions/{id}` 查询 | ✅ 7 项 M4 测试全过；跨智能体/跨用户隔离验证通过 |
| M5 审批流 | 1 周 | 注册自动开审批单；`/approval/{id}/decide` 真实决策（dept_admin 限本部门/重复决策 409）+ `/approval/pending` 待审列表；**发布红线：未 published 一律 403 不可调用**；dev 登录支持管理员身份切换 | ✅ 9 项 M5 测试全过；红线→审批→发布→可调用 全链路验证 |
| M6 基础设施接入 | 2 周 | ① 模型网关真生成（LiteLLM 探活+同步调用，离线 stub 兜底）② 会话/审批单 PG 写穿持久化（`pg_store`，重启载回）③ 知识文档 MinIO 对象存储（`object_store`，离线 memory 兜底）+ kb 列表/原文读回端点 | ✅ 6 项 M6 测试全过；compose up 后自动启用真设施，离线全链路不中断 |
| M7 计量真实化 | 2 周 | 每次调用落一单账（token 真值优先/成本写账固化/真实 or stub 可判别）+ 五维聚合（dept/agent/model/user/day，RBAC 收敛）+ 配额红线（30 天超限 invoke 429）+ 知识库上传（三处一致、删了不再召回）+ 会话/模型管理页 | ✅ 9 项 M7 测试全过；`usage_records` 直查可对账 |
| M8 试点就绪 | 2 周 | ① 批量语料导入（内容指纹去重/重跑幂等/失败逐条记录/索引只重建一次）② 知识库 PG 载回（重启不清零）③ 基线跑批（外部 golden 用标题反查、JSON+Markdown 报告、漂移历史）④ 成本曲线（逐日补零/月末外推/预算水位）⑤ 试点看板一屏 | ✅ 9 项 M8 测试全过；实测 250 篇导入 5.1 秒、重启仍 250 篇、加语料后 Recall@100 稳定 1.0 |

## M8 试点怎么跑（最快路径）

```bash
cd D:/enterprise-ai-platform/backend

# 1) 底座（PG 5432 / 对象存储 9000 / LiteLLM 4000）
./scripts/start_m6_stack.cmd -SkipValidate

# 2) 批量导入（先干跑看清单）；--scope department --dept d_sales
./.venv/Scripts/python.exe scripts/ingest_corpus.py --dir D:/corpus/sales --dry-run
./.venv/Scripts/python.exe scripts/ingest_corpus.py --dir D:/corpus/sales --scope department --dept d_sales

# 3) 基线评测（golden 只写 query + expected_title）
./.venv/Scripts/python.exe scripts/eval_baseline.py --golden D:/corpus/golden.jsonl --gate

# 4) 试点模式起后端（关演示语料，避免污染真实召回）
SEED_DEMO_CORPUS=0 ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

报告落 `backend/reports/`：`ingest_report.md`、`baseline.md`、`history.jsonl`（漂移）、`latest.json`（看板读它）。
完整两周 SOP 见 `docs/M8_试点运行手册.md`；没有真实语料时可先 `scripts/gen_pilot_corpus.py` 生成模拟语料压测链路。

## 当前骨架已验证
- `pytest`：64 项全过（6 骨架 + M1×5 + M2×7 + M3×2 + 最小闭环 e2e×1 + M4×7 + M5×9 + M6×6 + M7×9 + M8×9 + 其它）；含 Recall 门禁与客观门禁阻断逻辑验证；**PG 在位与离线两种模式均通过**
- M8 实机验证（2026-11-02，模拟语料）：导入 220 篇 **5.1 秒（43 篇/秒）**；重跑整批 **全部判重、78 ms**；重启后端 `/kb/stats` 仍为 220 篇；加 30 篇后 250 篇、Recall@100 仍 1.0、Recall@10 仍 0.8864
- 最小闭环端到端走查：`python scripts/e2e_walkthrough.py`（进程内串起 P0 三章，打印人读报告，无需起服务）
- **前端 React 联调已验证**：`npm run build` 通过（TS 编译 + Vite 打包 150KB/gzip 49KB）；选型向导改为异步调后端 `/selection/recommend`（本地规则兜底），`node web/scripts/contract_check.mjs` 复刻浏览器 fetch 链路（login→models→选型→召回→注册→**M4 对话/会话记忆**）全部通过；Vite dev server 经 `/api` 代理打到真实后端（127.0.0.1:8080），根页面 200、代理接口返回完整选型数据。
- **前端智能体对话页（M4）**：`web/src/pages/Chat.tsx` —— 多轮聊天 + 会话记忆可视化（轮次数/query 改写标记/会话记忆 JSON 查看）；智能体下拉从 `/agents` 实时拉取。
- `docker-compose.yml` YAML 合法；`web/package.json` / `tsconfig.json` 合法
- 注意：重客户端（qdrant/sqlalchemy/minio/redis/sentence-transformers/langgraph）为懒加载，
  未安装时提供确定性 stub，保证骨架可导入、可演示**单一过滤点**逻辑。
