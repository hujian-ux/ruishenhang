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
- **openapi.yaml（31 paths / 24 schemas）** ←→ `backend/app/routers/*` 一一映射
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
| M9 规模化 | 2 周 | ① 向量存 (N,D) numpy 矩阵 + 倒排预筛 BM25（`knowledge.py` 重写）② 增量写入 + tombstone 删除（不再全量重建）③ embedding sqlite 持久缓存 ④ 向量后端可插拔（memory/qdrant，失败静默降级）⑤ `/kb/index` 索引健康屏 + 试点看板卡 ⑥ golden 多目标命中（`accept_titles` 同族等价）⑦ 压测基准脚本 | ✅ 8 项 M9 测试全过（总 72）；2720 篇/33712 子块 检索 p50 **17.71ms**；2720 篇 golden Recall@5/10/100 全 **1.0**、MRR **0.9659** |
| M10 多租户规模化 | 2 周 | ① 候选阶段按身份预筛（同一份 `visible_fields`，第18章判权不复制）+ BM25 idf 粗排截断 3000 ② 异步导入任务 `/kb/ingest`（≤20000 篇/任务，进度/取消/列表）③ 对象存储 8 路并发写 ④ 索引读写加锁 ⑤ 多租户压测脚本 `bench_multitenant.py` | ✅ 7 项 M10 测试全过（总 79）；2485 篇/3 部门 × 6 身份**泄露 0、空结果 0、Recall@10 全 1.0**；p50 46ms→**19ms**；**1 万篇/122k 子块 p50 31.9ms** |
| M11 真实模型与真服务 | 1 周 | ① Qdrant Windows 原生二进制起真服务（不依赖 Docker），验 `QDRANT_MODE=http` 全链路 ② 接真实 bge-m3（1024 维，hf-mirror 离线下载）③ 批量编码 + query 走缓存 | ✅ 挖出并修掉 3 个真 bug（含一个让 Recall@100 跌到 0.948 跌破门禁、一个静默丢 97.6% 向量）；真实向量 Recall@5 0.9206→**0.9802**；缓存让检索 244ms→**10.5ms**、重导 303.6s→**4.5s** |
| M12 真实身份（企业 SSO/OIDC） | 3 天 | 身份认证从「dev token 单点」升级为**可插拔**：`security.decode_token` 增加 OIDC/JWT 校验分支（标准 discovery + JWKS 验签 RS256，对接任意 IdP 无需写死 SDK）；下游 `X-Caller-Context` 契约不变、零改动。**已选定 Casdoor 作中间件**（Apache-2.0 开源、自托管免费、原生接企业微信/钉钉）；已附 `docker-compose.casdoor.yml` + `docs/M12_Casdoor接入指南.md`。**前端已内置**：登录页（OIDC 授权码 + PKCE）、`/callback` 换码、`/setup` 自助连接页（填企微/钉钉资料 + 复制 Casdoor 清单 + 可选一键写入）；后端新增 `GET /auth/oidc/health`、`POST /auth/oidc/exchange`（后端代换码，避开 CORS）、`POST /auth/oidc/provision`（实验） | ✅ `test_m12_oidc.py` 6 项全过（claims→CallerContext 映射、过期/audience/签名拒绝、dev token 在 OIDC 开启时仍可用、生产环境拒绝 dev token）；前端 `tsc + vite build` 通过；未配 IdP 时只认 dev token，CI 不受影响 |
| M13 审计日志 | 2 天 | 新建 `audit` 模块（PG 落库 + SQLite 兜底，与计量解耦），`AuditTraceMiddleware` 生成 trace_id；在检索/生成/下载原文/审批决策四类动作落审计；`GET /audit/logs` 按 RBAC 收敛查询 | ✅ `test_m13_audit.py` 6 项全过（存储兜底、员工/部门/平台三级隔离、敏感字段脱敏）；跨请求 trace 可串联 |
| M14 第二部门真实语料试点 | 2 天 | 固化「导入双部门→评估→跨域隔离校验」SOP（同一索引按 caller 可见性预筛，非分库存）；`scripts/pilot_second_dept.py` 一键跑通，真实数据到位直接换目录 | ✅ 实跑：双部门各 200 篇共 400 篇、隔离计数正确；第二部门 Recall@100=1.0、过滤正确率 1.0；**跨域泄露探针 Recall@5=0.0（零泄露）**；门禁 PASS。注：@5/MRR 低为伪向量下限，真实 bge-m3 下 M11 已验 @5=0.98 |
| M15 技能包融合（方案库接入） | 3 天 | 把 `D:\workbuddy_解决方案` 的 **115 个 prompt-as-skill 技能包**接进平台：`agent_store`（内存+PG 写穿，保留 system_prompt/分类/输入产出契约）、`agent_runtime` 按 agent 加载提示词（不再全局写死）、`POST /agents/import` 批量导入**合并一张批次审批单**、`GET /agents/catalog` 分类目录、前端 `Skills.tsx` 目录页。红线不放宽：未 published 仍 403 | ✅ `test_m15_skills.py` 7 项全过（字段不丢、批次单唯一、审批前403/审批后 `skill_applied=true`、目录不串行业、幂等重导入、跨部门隔离、global 可见）；全量 **98 passed**；前端 `tsc + vite build` 通过。实测：115 个一次导入 → 1 张批次单 → 一次审批迁移 115 个。详见 `docs/M15_技能包融合方案.md` |

| M16 文件进出（P0） | 3 天 | 技能包本质是 **xlsx 进 → xlsx 出**，而 invoke 原来只吃 `input: str`，20 列的台账根本进不来。新增：`POST /agent/files` 上传（上传即解析，回表头/列画像/样本）、`invoke.file_ids` 引用附件、`tabular`（标题行识别 + 列类型/空值率 + 预算内压缩，不把整表塞 prompt）、`artifact`（答案里的 markdown 表/JSON 数组 → 多 sheet xlsx，永远有「执行结论」页）、`GET /agent/files/{id}` 后端代理下载（带鉴权，不暴露 MinIO）、`file_store`（内存+PG 写穿，重启仍可下载） | ✅ `test_m16_files.py` 17 项全过（标题行干扰下表头仍准、损坏文件降级不崩、全链路上传→执行→产出物→下载、跨部门 404、产出物生成器多表/JSON/兜底）；全量 **115 passed**；前端 `tsc + vite build` 通过。真实对象存储路径已验（`store: minio`，S3 协议往返）；顺带修掉两处旧 bug：`pg_store._KEY_COL` 漏 `agents` 导致 M15 写穿 PG 静默失效、`invoke` 的 `audit.record("generate")` 写在 `return` 之后是从不可达代码 |

> 接口数：openapi **37 paths / 27 schemas**。测试累计 **115 passed**（M0–M16）。

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

## M9 规模化怎么验（2000+ 篇不塌）

```bash
cd D:/enterprise-ai-platform/backend

# 1) 压测：全量导入 / 增量 / 检索延迟（输出 reports/index_bench_*.json）
./.venv/Scripts/python.exe scripts/bench_index.py \
  --dirs D:/pilot_corpus/docs D:/pilot_corpus_big/docs --inc 50 --repeat 10 \
  --out reports/index_bench_scale.json

# 2) 索引健康（进程内直查，不用起服务）
./.venv/Scripts/python.exe -c "from app.services import knowledge; print(knowledge.index_health())"

# 3) 规模化后端起服务后看一屏：GET /api/v1/kb/index（试点看板「索引健康」卡同源）
VECTOR_BACKEND=memory EMBED_CACHE=1 ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8080
```

| 开关 | 默认 | 说明 |
|------|------|------|
| `VECTOR_BACKEND` | `memory` | `memory`（进程内 numpy）/ `qdrant` / `auto`（先试 qdrant，失败降级） |
| `QDRANT_MODE` | `http` | `local` 用 qdrant-client 文件模式，不占端口也能验真实引擎 |
| `EMBED_CACHE` | `1` | sqlite 单文件缓存（`data/embed_cache.db`），键 = model+dim+text sha1 |
| `INDEX_MAX_CHILDREN` | `200000` | 子块容量上限（约 1000–2000 篇），超限前 `/kb/index` 的 `capacity_used_pct` 会先告警 |
| `INDEX_COMPACT_RATIO` | `0.25` | tombstone 占比超此值自动压缩索引（删多写少时回收内存） |
| `USE_LOCAL_EMBEDDING` | `0` | `1` 启用真实 bge-m3；`0` 走确定性伪向量（测试/离线，毫秒级但语义为假） |
| `EMBEDDING_MODEL` | `bge-m3` | 可填本地绝对路径，如 `D:/models/bge-m3`，配合 `HF_HUB_OFFLINE=1` 完全离线 |
| `EMBED_BATCH_SIZE` | `16` | 真实模型编码批大小（CPU 上 16 最稳） |
| `QDRANT_TIMEOUT_S` | `5.0` | 别调回 1 秒——首次建集合会超时并静默降级 |
| `QDRANT_ASYNC_UPSERT` | `0` | 默认同步写；改 `1` 后「写完立刻检索」会看不到数据 |

改造前后对比、规模化边界与降级策略见 `docs/M9_规模化报告.md`。

## M10 多租户怎么验（三个部门共用一套索引）

```bash
cd D:/enterprise-ai-platform/backend

# 多租户压测：3 部门 × 800 篇 + 其他租户噪声 + 6 个身份探针（泄露/空结果/Recall/延迟）
./.venv/Scripts/python.exe scripts/bench_multitenant.py --per-dept 800
./.venv/Scripts/python.exe scripts/bench_multitenant.py --per-dept 100 --with-pg   # 连真 PG

# 异步导入（服务内万篇级；同步 bulk 一次只收 50 个文件）
curl -X POST http://127.0.0.1:8080/api/v1/kb/ingest -H "Authorization: $TK" \
     -H "Content-Type: application/json" \
     -d '{"documents":[{"title":"x","content":"..."}],"scope":"department"}'
curl http://127.0.0.1:8080/api/v1/kb/ingest/<job_id> -H "Authorization: $TK"     # 轮询进度
```

要点：**过滤在候选阶段就做了**（`_vis_mask`），`filtered_out` 因此通常是 0——那是兜底计数，不是「过滤没生效」。多租户细节与压测数据见 `docs/M10_多租户规模化报告.md`。

## M11 真实模型怎么开（含一个必须知道的前提）

```bash
cd D:/enterprise-ai-platform/backend

# 真实 bge-m3：三个开关缺一不可，否则静默退回伪向量（数字全假）
USE_LOCAL_EMBEDDING=1 EMBEDDING_MODEL=D:/models/bge-m3 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 EMBED_CACHE=1 \
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8080

# Qdrant 真服务（Windows 原生二进制，不需要 Docker）
cd /d/tools/qdrant && ./qdrant.exe      # 6333 HTTP / 6334 gRPC
VECTOR_BACKEND=qdrant QDRANT_MODE=http ./.venv/Scripts/python.exe -m pytest -q
```

**最重要的一条**：M9/M10 报的 `3.35ms`、`19ms` 都**不含 embedding**。真实模型下
`query 编码（244ms 首次 / 缓存后 <1ms）+ 索引检索` 才是用户感知延迟。
完整数据与 3 个 bug 的复现过程见 `docs/M11_真实模型与Qdrant验证.md`。

## 当前骨架已验证
- `pytest`：**79 项全过**（6 骨架 + M1×5 + M2×7 + M3×2 + 最小闭环 e2e×1 + M4×7 + M5×9 + M6×6 + M7×9 + M8×9 + M9×8 + M10×7 + 其它）；含 Recall 门禁与客观门禁阻断逻辑验证；**PG 在位与离线两种模式均通过**（`tests/conftest.py` 默认把 PG 指向不可达端口做隔离，`TEST_WITH_PG=1` 跑真 PG）
- M10 多租户实机验证（2026-11-02，模拟语料）：2485 篇 / 3 部门 / 30 358 子块，6 个身份 × 24 条 query —— **泄露 0、空结果 0、Recall@10 全 1.0**、p50 19–21ms；压测暴露并修掉「过滤在聚合之后导致空结果」（5→0，p50 46ms→19ms）；**10 000 篇 / 122 084 子块 / 500MB，p50 31.9ms、p95 71ms**
- M9 规模化实机验证（2026-11-02，模拟语料）：220 篇/2686 子块 检索 p50 **133ms → 3.35ms**；2720 篇/33712 子块 p50 **17.71ms**、p95 21.1ms；增量写入 20 篇/批 **0.018s**（改造前 0.313s）；导入 **1100 篇/秒**；golden 补 `accept_titles` 后 Recall@5/10/100 全 1.0、MRR 0.9659（原 Recall@5 0.5909 的「坏例」经排查**全部是同族等价，真漏检 0 条**）
- M8 实机验证（2026-11-02，模拟语料）：导入 220 篇 **5.1 秒（43 篇/秒）**；重跑整批 **全部判重、78 ms**；重启后端 `/kb/stats` 仍为 220 篇；加 30 篇后 250 篇、Recall@100 仍 1.0、Recall@10 仍 0.8864
- 最小闭环端到端走查：`python scripts/e2e_walkthrough.py`（进程内串起 P0 三章，打印人读报告，无需起服务）
- **前端 React 联调已验证**：`npm run build` 通过（TS 编译 + Vite 打包 150KB/gzip 49KB）；选型向导改为异步调后端 `/selection/recommend`（本地规则兜底），`node web/scripts/contract_check.mjs` 复刻浏览器 fetch 链路（login→models→选型→召回→注册→**M4 对话/会话记忆**）全部通过；Vite dev server 经 `/api` 代理打到真实后端（127.0.0.1:8080），根页面 200、代理接口返回完整选型数据。
- **前端智能体对话页（M4）**：`web/src/pages/Chat.tsx` —— 多轮聊天 + 会话记忆可视化（轮次数/query 改写标记/会话记忆 JSON 查看）；智能体下拉从 `/agents` 实时拉取。
- `docker-compose.yml` YAML 合法；`web/package.json` / `tsconfig.json` 合法
- 注意：重客户端（qdrant/sqlalchemy/minio/redis/sentence-transformers/langgraph）为懒加载，
  未安装时提供确定性 stub，保证骨架可导入、可演示**单一过滤点**逻辑。
