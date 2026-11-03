# M12 真实身份接入指南（Casdoor 版）

> 选型结论：用 **Casdoor** 作为标准 OIDC 中间件 —— Apache-2.0 开源、**自托管完全免费、不限用户数**、**原生内置企业微信/钉钉身份源**、对外吐标准 OIDC（RS256 + JWKS + PKCE）。
> 本平台 M12 的 `decode_token` 已基于"标准 discovery + JWKS 验签"实现，对接 Casdoor **零代码改动**，只需填 issuer + client_id 两个值。

---

## 0. 为什么是 Casdoor（而不是 Authing）

| 维度 | Authing | **Casdoor（本方案）** |
|---|---|---|
| 费用 | 企业微信/钉钉连接器只在**企业版**（付费，需联系销售）才有；免费版内部员工仅 10 人且不含企业身份连接 | **Apache-2.0 开源，自托管免费，不限 MAU** |
| 自建 | 仅企业版加购私有化 | **Docker 一条命令起** |
| 企业微信/钉钉 | 仅企业版内置 | **原生内置身份源，控制台填密钥即用，无需插件** |
| 对外协议 | 标准 OIDC | **标准 OIDC（RS256 / JWKS / PKCE）** |
| 对 M12 的影响 | 同 | **零改动，只换 issuer + client_id** |

---

## 1. 部署 Casdoor（二选一）

### 方案 A：30 秒试用（内置 SQLite，数据在容器内，重启即丢，仅体验）
```bash
docker run -p 8000:8000 casbin/casdoor-all-in-one
```
打开 http://127.0.0.1:8000 ，用 **组织 `built-in` / 用户 `admin` / 密码 `123`** 登录。

### 方案 B：持久化部署（Postgres，推荐，数据不丢）—— 已附 `docker-compose.casdoor.yml`
```bash
# 在项目根目录执行
docker compose -f docker-compose.casdoor.yml up -d
```
- 管理后台： http://127.0.0.1:8000 （账号同上）
- 仅把 8000 发布到本机，数据库在 compose 内部网络，不与你本机已有的 PostgreSQL 冲突。
- **首次登录务必改 admin 密码**（Casdoor 控制台 → 用户 → admin → 改密）。

> 生产环境：把 8000 放到反向代理后、强制 HTTPS、用真实域名；`OIDC_ISSUER` 同步换成 `https://你的域名`。

---

## 2. 在 Casdoor 里建应用，拿到 M12 要的两个值

1. 控制台左侧 **Applications** → 新建（或编辑自带的 `app-built-in`）。
2. 记下 **Client ID**（这就是 M12 的 `client_id` / `OIDC_AUDIENCE`）。Client Secret 公钥流用不到，先存着。
3. **Redirect URLs** 增加一条（前端回调地址，开发期）：
   ```
   http://127.0.0.1:5173/callback
   ```
   生产再加 `https://你的前端域名/callback`。
4. **Signing algorithm / 签名算法** 保持 **RS256**（M12 的 JWKS 公钥验签只认 RS256）。
5. 保存。

> M12 的 `OIDC_ISSUER` = 你的 Casdoor 地址，且**必须和 token 里的 `iss` 完全一致**（含协议、主机、端口，无末尾斜杠）：
> ```
> OIDC_ISSUER=http://127.0.0.1:8000
> ```

---

## 3. 接入企业微信 / 钉钉（让登录页出现对应入口）

1. 控制台左侧 **Identity Providers** → **Add** → 选 **WeCom**（企业微信）或 **DingTalk**（钉钉）。
2. 填对应密钥：
   - 企业微信： **CorpID** + 自建应用的 **Secret**（取自企业微信管理后台：我的企业→企业信息→企业ID；应用管理→自建应用→AgentId/Secret）。
   - 钉钉： **Client ID（AppKey）** + **Client Secret（AppSecret）**（取自钉钉开发者后台：应用开发→应用详情→应用信息）。
3. 把该身份源**关联到第 2 步建的应用**。
4. 之后在 Casdoor 登录页（或我们自建登录页）会出现企业微信/钉钉入口；员工用两家账号登录后，Casdoor 签发标准 OIDC 的 ID token 给本平台。

---

## 4. 后端配置（填进 backend 的环境变量 / `.env`）

```bash
OIDC_ENABLED=1
OIDC_ISSUER=http://127.0.0.1:8000          # 与 Casdoor 的 iss 完全一致
OIDC_AUDIENCE=<第2步拿到的 Client ID>       # Casdoor Application 的 Client ID
OIDC_DEPT_CLAIM=affiliation                 # Casdoor 用 affiliation 字段承载组织名
OIDC_ROLE_CLAIM=role                        # 留默认即可；见下方角色映射说明
# OIDC_TENANT_CLAIM=                         # 留空→回退 DEFAULT_TENANT
```

### 角色映射（已内置，无需在 Casdoor 侧建角色名字段）
本平台只认 `employee | dept_admin | platform_admin` 三种角色，M12 的 `_resolve_role` 按以下优先级判定：
1. 若 IdP 直接下发上述三值之一（字符串或数组）→ 直接用；
2. 否则按 Casdoor 的布尔标志推导：
   - `isGlobalAdmin = true` → **platform_admin**（看全部审计/全部部门）
   - `isAdmin = true` → **dept_admin**（部门管理员）
   - 都没有 → **employee**

> 试用阶段全员默认 `employee` 即可；需要分级时，在 Casdoor 用户里勾选 "Is Admin" / "Is Global Admin"。

---

## 5. 前端登录（下一步要写，本指南先说清接口契约）

前端实现 **Authorization Code + PKCE** 标准 OIDC 流：
1. 登录按钮 → 跳 Casdoor `authorization_endpoint`（`/login/oauth/authorize`，带 `client_id`/`redirect_uri`/`scope=openid profile email`/`code_challenge`）。
2. Casdoor 回调 `http://127.0.0.1:5173/callback?code=...` → 前端用 code 换 token（`/api/login/oauth/access_token`），取 **id_token**。
3. 之后每次调本平台 API，把 **id_token 放进 `X-Caller-Context` 头**（正好对上 M12 已写好的 `decode_token`）：
   ```
   X-Caller-Context: <id_token>
   ```
4. 生产环境（`APP_ENV=prod`）后端会拒绝 `dev` 前缀令牌，强制走真实 OIDC。

> 这步是 M12 "收口" 的最后一块。你确认 Casdoor 起好、Client ID 拿到后，我即写前端登录页 + 回调换码，并补一个 `/auth/oidc/health` 自检端点方便你验证 JWKS 可达。

---

## 6. 验证 M12 链路是否通

后端起好后，无需前端即可先验证 IdP 可达、JWKS 能拉到：
```
GET /api/v1/auth/oidc/health
```
返回 `{"enabled":true,"ok":true,"issuer":"http://127.0.0.1:8000"}` 即说明 discovery + JWKS 正常。

---

## 7. 排错速查

| 现象 | 原因 / 处置 |
|---|---|
| `oidc/health` 的 `ok=false` | issuer 填错（必须 = token 的 `iss`，含端口、无末尾斜杠）；或 Casdoor 没起来 / 网络不通 |
| token 验签失败、`decode_token` 返回 None | Casdoor 签名算法不是 RS256；或 JWKS 缓存了旧 key（重启后端清缓存） |
| audience 不匹配被拒 | `OIDC_AUDIENCE` ≠ Casdoor Application 的 Client ID |
| 企业微信/钉钉登录不出现 | 身份源没关联到该 Application；或 CorpID/Secret 填错、可信域名未配 |
| 角色全是 employee | 正常（默认）；要分级就在 Casdoor 用户勾 Is Admin / Is Global Admin |

---

## 8. 前端登录页与自助连接（已内置）

前端已内置完整 OIDC 登录链路，无需改代码，只填配置即可用：

1. **登录页 `/`**：未登录时显示「使用企业身份登录」（企业微信/钉钉/SSO）与「开发模式」入口。
   - 企业身份登录走标准 **Authorization Code + PKCE(S256)**，跳转 Casdoor 授权页。
   - Casdoor 登录页会展示你在其后台配好的企业微信/钉钉入口。
2. **回调页 `/callback`**：前端拿到 `code` 后调用后端 `POST /api/v1/auth/oidc/exchange` 换 `id_token`
   （放后端换码，避开浏览器 CORS；client_secret 不出后端），随后把 `id_token` 作为 `Authorization` 头发给网关验签。
3. **连接配置页 `/setup`（自助）**：任何人可在此填
   - Issuer（Casdoor 地址，默认占位 `http://127.0.0.1:8000`）
   - Client ID（Casdoor 应用 App ID）
   - 企业微信 CorpID / Secret / AgentId、钉钉 AppKey / AppSecret
   - 点「保存连接配置」→ 存本地，登录按钮立即可用；
   - 点「复制 Casdoor 填写清单」→ 得到一份照抄即用的后台填写文本；
   - 可选「一键写入 Casdoor（实验）」→ 用 Casdoor 管理员账号自动建 IdP 并关联应用（失败则用手动清单，保证可用）。

> 关键：前端填的 `issuer` / `client_id` 会用于发起授权；后端验签以环境变量 `OIDC_ISSUER` / `OIDC_AUDIENCE`
> 为准（未设时按 token 自带 `iss` 验签，适合自服务/演示）。生产建议两端填同一组值并固定 `OIDC_ISSUER`。

### 新增后端端点
| 端点 | 作用 |
|---|---|
| `POST /api/v1/auth/oidc/exchange` | 后端代前端用授权码换 `id_token`（PKCE） |
| `POST /api/v1/auth/oidc/provision` | 实验性：把企微/钉钉资料一键写入 Casdoor（需管理员账号） |

### 生产部署注意
`/callback`、`/setup` 是前端路由，Web 服务器需配置 **SPA fallback**（所有未匹配路径回落到 `index.html`），
否则刷新 `/callback` 会 404。Vite dev / preview 已内置该回落。
