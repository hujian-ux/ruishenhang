# 集团租户物理隔离 · 部署指南

企业 AI 中台支持两种租户隔离形态。**默认且已生产验证**的是**逻辑多租户**（单栈、按
`tenant_id` 隔离，已通过 M43/M43b 跨租户回归测试）；**物理隔离**面向金融/政务/大集团客户，
要求「数据不出租户、监管可审计的物理分离」。本文给出后者的落地模板与决策依据。

---

## 一、两种形态对比

| 维度 | 逻辑多租户（默认） | 物理隔离（本指南） |
|---|---|---|
| 数据位置 | 同一 PG/Qdrant/MinIO，按 `tenant_id` 行级/集合级隔离 | 每租户独立 PG/Qdrant/MinIO/Redis 实例，卷互相独立 |
| 网络 | 共享容器网络 | 每租户独立 Docker 网络（`${TENANT}_net`），彼此不可达 |
| 故障爆炸半径 | 单栈故障影响所有租户 | 单租户栈故障仅影响该租户 |
| 合规 | 满足一般数据主权 | 满足等保/监管「物理隔离」硬要求 |
| 成本 | 低（一套资源） | 高（每租户一套底座，建议每栈独占主机/命名空间） |
| 上线速度 | 快（开账号即开通） | 中（编排独立栈 + 独立密钥） |
| 适用 | 中小企 SaaS、同集团内常规部门 | 集团子公司独立核算、金融/政务、竞品互斥客户 |

**结论**：绝大多数客户用逻辑多租户即可；只有被监管或合同明确要求「物理隔离」时，才走本指南。

---

## 二、物理隔离落地（单租户独立栈）

每个租户一套**完全独立**的底座（PG / Qdrant / MinIO / Redis / backend）+ 独立网络与卷。
数据在磁盘上分卷、网络上跨栈不通，满足物理隔离。

### 2.1 生成独立栈

```bash
# 在项目根目录执行（脚本读取 deploy/docker-compose.tenant.template.yml）
python backend/scripts/gen_tenant_stack.py --tenant acme --prod --out-dir deploy
```

产出（写入 `deploy/`）：
- `docker-compose.acme.yml` — 独立栈编排（服务名/卷名/网络名均带 `acme_` 前缀）
- `.env.acme` — 该租户专属环境变量，**含随机数据库密码与 MinIO 密钥**（已尝试 `chmod 600`）

同主机多栈时加 `--port-offset 1000`（端口错开，避免冲突）；`--print` 可仅预览不写文件。

### 2.2 启动独立栈

```bash
cd deploy
docker compose -f docker-compose.acme.yml --env-file .env.acme up -d
# 校验
curl http://localhost:8080/healthz
```

### 2.3 环境变量隔离清单（务必逐项核对）

| 变量 | 隔离要求 |
|---|---|
| `PG_HOST` / `PG_PASSWORD` / `PG_DB` | **每租户独立实例 + 独立强密码**，禁止跨栈复用 |
| `QDRANT_HOST` | 指向本栈 `${TENANT}_qdrant`，不连其它租户向量库 |
| `MINIO_ENDPOINT` / `MINIO_USER` / `MINIO_PASSWORD` | 独立对象存储 + 独立密钥；产出物与上传件不串租户 |
| `REDIS_URL` | 独立 Redis 实例（限流/缓存/会话不跨租户） |
| `CALLER_SIGNING_SECRET` | **每栈独立随机值**（网关 HMAC 信任边界） |
| `WEBHOOK_TOKEN_SECRET` | 同上，Webhook 验签 |
| `OIDC_ISSUER` / `CORS_ALLOW_ORIGINS` | 按该租户 SSO 与前端域名配置 |
| `LITELLM_MASTER_KEY` | 模型网关主密钥独立；模型 key 按租户注入 |
| `AUDIT_EXPORT_KEY` | 审计加密导出密钥独立（合规留痕） |

> 生成器已为 PG/MinIO 写入随机密钥；`CALLER_SIGNING_SECRET`、`OIDC_ISSUER`、
> `CORS_ALLOW_ORIGINS`、`LITELLM_MASTER_KEY`、`AUDIT_EXPORT_KEY` 需**人工补填**
> （生产自检 `main._fail_if_insecure_for_prod` 会在这些值缺失/弱时拒绝启动）。

### 2.4 备份与销毁

- 备份：直接对各自卷（`acme_pgdata` 等）做快照/导出；各租户独立，互不影响。
- 销毁：停栈 + 删卷即可彻底清除该租户全部数据，无残留风险。

---

## 三、Kubernetes 形态（生产推荐）

容器编排同理：每个租户一个独立 **Namespace**（或独立集群），内部署 PG/Qdrant/MinIO/Redis/
backend 的 StatefulSet/Deployment + 独立 PVC。网络策略（NetworkPolicy）禁止跨命名空间访问。
生成器产出的 compose 可 1:1 映射为 K8s 清单（或经 kompose 转换后补全 PVC/NetworkPolicy）。

---

## 四、决策清单（给售前/交付）

1. 客户是否被监管/合同要求**物理隔离**？否 → 逻辑多租户（默认）。
2. 是 → 每租户独立栈，确认是否有独立主机/命名空间预算。
3. 确认备份、销毁、密钥轮换 SOP（见 2.3 / 2.4）。
4. 逻辑多租户已自动化开通（`/tenants`），物理隔离走本生成器 + 人工密钥补填。
