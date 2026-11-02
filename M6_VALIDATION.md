# M6 基础设施实机验证指南

> 目标：在本机用 `docker compose` 起好 PostgreSQL / MinIO / LiteLLM 三底座，
> 验证「会话·审批单写穿 PG 重启不丢」「知识文档落 MinIO 可回读」「智能体真实 LLM 生成」。
> 沙箱无 Docker，本验证**必须在你本机**执行。

---

## 0. 前置：本机装好 Docker Desktop

```powershell
docker --version
docker compose version
```
两条都正常输出即 OK。没有就去装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，装完**重启一次**让 WSL2/守护进程就绪。

> **若提示 `docker : 无法将"docker"项识别为 cmdlet...`**：说明本机没装 Docker（或装了但没加进 PATH）。
> Docker 是跑 PG/MinIO/LiteLLM 三底座的前提。两条路：
> - **A（推荐，完整验证）**：装 Docker Desktop，重启后重开 PowerShell 再试；
> - **B（只想先看真 LLM 生成）**：不装 Docker，走第 3.1 节的 pip 轻量路径，
>   PG/MinIO 两项会 `[SKIP]`，但真 LLM 生成能跑通。

---

## 1. 启动三底座

在仓库根目录（含 `docker-compose.yml`）执行：

```powershell
cd D:\enterprise-ai-platform

# 可选：填写密钥（想跑出"真实 LLM 生成"才需要，见第 3 节）
# 在仓库根目录新建 .env（compose 会自动读取），内容示例：
#   DASHSCOPE_API_KEY=sk-你的通义千问密钥
#   OPENAI_API_KEY=
#   PG_PASSWORD=dev
#   MINIO_USER=minioadmin
#   MINIO_PASSWORD=minioadmin

docker compose up -d
docker compose ps          # 看 STATUS 是否 healthy/running
```

- `postgres:16` 首次启动会自动执行 `infra/init/*.sql` 建表（会话表、审批单表、ACL）。
- `minio` 控制台在 http://127.0.0.1:9001 （账号 minioadmin/minioadmin）。
- `litellm` 在 http://127.0.0.1:4000 ；配置见 `backend/app/services/model_gateway/litellm_config.yaml`。

> 镜像首次拉取较慢（postgres/minio/litellm 几个 G），耐心等 `docker compose ps` 全 up。

---

## 2. 跑自动化验证脚本（推荐先做）

```powershell
cd D:\enterprise-ai-platform\backend
# 真实 LLM 生成想用通义千问，先设默认模型（不设为 gpt-4o-mini，需 OPENAI 密钥）
$env:LLM_DEFAULT_MODEL="qwen-plus"

.\.venv\Scripts\python.exe scripts\validate_m6.py
```

脚本逐项输出 `[PASS]/[FAIL]/[SKIP]`：
- **PG 写穿 + 重启载回**：脚本会写一条会话 + 一张审批单到 PG，再**另起一个进程模拟应用重启**，从 PG 载回验证不丢。
- **MinIO 上传 + 回读一致**：写一段文档原文到 `kb-files` 桶，再读回比对。
- **LiteLLM 可达 + 智能体调用**：探活 4000 端口；可达则直接调 `agent_runtime`，输出里标 `真实LLM生成` 或 `stub回退`。

> 若某项 `[SKIP]`：说明对应底座没起来（端口不通），接口仍可运行只是走内存兜底，不影响平台使用。

---

## 3. 想看到「真实 LLM 生成」需要密钥

`docker-compose.yml` 已把 `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` 注入 litellm 容器。
国内最省事是**通义千问**：

1. 去阿里云百炼申请 `DASHSCOPE_API_KEY`（DashScope）。
2. 在仓库根目录 `.env` 写 `DASHSCOPE_API_KEY=sk-xxx`，然后 `docker compose up -d` 让它生效；
   或直接在跑 litellm 的终端 `export` 后重启容器。
3. 验证脚本里把 `LLM_DEFAULT_MODEL` 设为 `qwen-plus`（见第 2 节）。
4. 重跑脚本：输出里出现 `真实LLM生成` + 一段通顺中文，即真生成生效。

> 没密钥也不影响：模型网关会优雅回退到确定性 stub（答案带 `[stub:...]`），平台其余功能照常。

### 3.1 不装 Docker，只用 pip 跑真 LLM 生成（轻量路径）

LiteLLM 代理可以不经 Docker 直接 pip 安装：

```powershell
cd D:\enterprise-ai-platform\backend
.\.venv\Scripts\python.exe -m pip install "litellm[proxy]"

# 终端 C：起 litellm 代理（读同一份模型配置）
$env:DASHSCOPE_API_KEY="sk-你的通义千问密钥"
$env:LLM_DEFAULT_MODEL="qwen-plus"
.\.venv\Scripts\python.exe -m litellm --config app\services\model_gateway\litellm_config.yaml --host 127.0.0.1 --port 4000
```

然后新开一个终端跑验证脚本（此时 PG/MinIO 会 SKIP，LiteLLM 一项应 PASS 且显示"真实LLM生成"）：

```powershell
cd D:\enterprise-ai-platform\backend
$env:LLM_DEFAULT_MODEL="qwen-plus"
.\.venv\Scripts\python.exe scripts\validate_m6.py
```

---

## 4. 手动「真重启不丢」走查（最有说服力）

用真实后端进程验证「重启不丢会话/审批单」：

```powershell
# 终端 A：起后端
cd D:\enterprise-ai-platform\backend
$env:LLM_DEFAULT_MODEL="qwen-plus"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

```powershell
# 终端 B：用 dev 令牌调接口
$token = (.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from app.core.security import create_dev_token; print(create_dev_token(user_id='u_val', dept_id='d_val', role='employee'))")

# ① 建智能体（自动开审批单）→ ② 管理员通过 → ③ 发对话（生成会话记忆）
$body = '{"name":"验证智能体","adapter_type":"llm_retrieval","description":"m6","knowledge_scope":"department"}'
$r = Invoke-RestMethod -Uri http://127.0.0.1:8080/api/v1/agents -Method Post -ContentType application/json -Headers @{"Authorization"=$token} -Body $body
$agentId = $r.data.agent_id; $aprId = $r.data.approval.approval_id
Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/approval/$aprId/decide" -Method Post -ContentType application/json -Headers @{"Authorization"=$token} -Body '{"decision":"approve","decider_id":"wang","decider_role":"platform_admin","decider_dept":"d_val"}'
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/v1/agent/invoke -Method Post -ContentType application/json -Headers @{"Authorization"=$token} -Body "{`"agent_id`":`"$agentId`",`"input`":`"知识库检索怎么实现`"}"
```
记下返回的 `session_id`。

**现在去终端 A 按 Ctrl+C 关掉后端，再重新起一次同样的 uvicorn 命令（模拟重启）。**

```powershell
# 重启后，会话记忆与审批单应从 PG 载回，不丢：
Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/agent/sessions/<刚才的session_id>" -Headers @{"Authorization"=$token}
Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/v1/approval/pending" -Headers @{"Authorization"=$token}
```
能看到之前的会话多轮内容与审批单 → 「重启不丢」验证通过。

---

## 5. 收尾

```powershell
docker compose down        # 停底座（数据在卷里保留，下次 up 还在）
# 彻底清数据：docker compose down -v
```

## 6. 常见坑

- **`docker` 命令不存在**：没装 Docker Desktop（或未加入 PATH）。见第 0 节 A/B 两条路。
- **`..\.venv\Scripts\python.exe` 报"无法识别"**：venv 在 `backend\.venv`，在 `backend` 目录下要用 **`.\.venv\Scripts\python.exe`**（单点不是双点）。
- **端口被占用**：5432/9000/9001/4000/6379 被本机其他服务占了 → 改 `docker-compose.yml` 左侧端口映射。
- **litellm 起不来**：`:latest` 偶发不兼容，可在 compose 里把 `litellm/litellm:latest` 固定到某个版本标签。
- **MinIO 桶不存在**：应用首次调用会自动 `make_bucket`，无需手动建。
- **PG 表没建**：确认 `./infra/init` 已挂载到 `/docker-entrypoint-initdb.d`；只有**首次**建库时执行，已存在的数据卷不会重复执行。
