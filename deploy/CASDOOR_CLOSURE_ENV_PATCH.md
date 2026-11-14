# Casdoor 身份收口 — `.env` 实际改动草稿（P0）

> 本文件是「准备类」交付物：**只描述要往 `.env` 追加哪些行，不修改任何活体文件**。
> 实际改动请在你的**生产环境** `.env` 上执行（切勿在当前 dev `.env` 上直接开 `AUTH_ENFORCE_OIDC=1`，否则 dev 后门被禁又无可用 SSO → 全员锁死）。
> 所有变量名/默认值均来自 `backend/app/core/config.py`（行号见下表），与代码逐行对齐。

---

## 0. 当前状态（实查）
- 当前 `backend/.env`（即根目录 `.env`）**不含任何** `APP_ENV / OIDC_* / AUTH_ENFORCE_OIDC / CASDOOR_*` 键，只有 `LLM_*` 与 `WEB_SEARCH_*`（`grep` 已验证）。
- 因此这些开关**全部走 config.py 默认值**：`OIDC_ENABLED=0`（关闭）、`APP_ENV="dev"`、`AUTH_ENFORCE_OIDC` 在 dev 下 = `False`、`/auth/sso/callback` 仍接受 `dev_admin` 捷径。
- 结论：**收口远未做**，真实缺口是「往 `.env` 追加下面这批键并按序翻开关」。

---

## 1. 致命顺序（必须先看）

`backend/app/core/security.py:10` 明确：OIDC 未启用时「只认 dev token」；`security.py:192/195` 在 `AUTH_ENFORCE_OIDC=1` 或 `APP_ENV=prod` 时拒绝 dev token。
因此若**先开收口、OIDC 却没启用** → dev token 被拒、OIDC 分支又不受理 → **全员无法登录**。

**唯一安全路径：先启用 OIDC 并验证 SSO 真能登录 → 再翻收口开关。**

---

## 2. 分阶段追加块（可直接 `cat >> .env`）

```bash
# ===== Casdoor 身份收口（P0）— 分阶段追加，严禁一次全开 =====

# —— 阶段 1：启用 OIDC，但仍不收口（dev_admin 后门保留，用于灰度对照）——
APP_ENV=prod
OIDC_ENABLED=1
# Casdoor 地址，须与下发 id_token 的 iss 完全一致（通常不含末尾斜杠）
OIDC_ISSUER=https://casdoor.your-domain.com
# 本平台在 Casdoor 注册的 Application 的 Client ID（验签 aud）
OIDC_AUDIENCE=<CASDOOR_APP_CLIENT_ID>
# 后端换码用 client_id；留空则回退 OIDC_AUDIENCE
OIDC_CLIENT_ID=<CASDOOR_APP_CLIENT_ID>
# 前端回调地址，须与 Casdoor 应用登记的 Redirect URLs 完全一致
OIDC_REDIRECT_URI=https://ai.your-domain.com/callback
OIDC_DISCOVERY_TIMEOUT=3.0
# claim 映射（Casdoor 默认字段，按需调整）
OIDC_USER_CLAIM=preferred_username
OIDC_DEPT_CLAIM=affiliation
OIDC_ROLE_CLAIM=role
OIDC_TENANT_CLAIM=
OIDC_NAME_CLAIM=name
# 阶段 1 显式不收口：dev_admin 仍可用，用于验证 SSO 前不锁死任何人
AUTH_ENFORCE_OIDC=0

# —— 阶段 2：确认 /auth/oidc/health 与真实 SSO 登录都 OK 后，再翻这一行 ——
# AUTH_ENFORCE_OIDC=1
```

> 可选收尾（非必须）：`SEED_DEMO_CORPUS=0`（生产不再灌演示语料）、`DEFAULT_TENANT=<你的主租户>`（OIDC_TENANT_CLAIM 留空时回落到此）。

---

## 3. 变量 ↔ config.py 行号映射

| 变量 | config.py 行 | 默认 | 本草稿目标 |
|---|---|---|---|
| `APP_ENV` | 51 | `dev` | `prod` |
| `OIDC_ENABLED` | 217 | `0` | `1` |
| `OIDC_ISSUER` | 218 | `""` | Casdoor 地址 |
| `OIDC_AUDIENCE` | 219 | `""` | 应用 Client ID |
| `OIDC_CLIENT_ID` | 221 | `""` | 应用 Client ID |
| `OIDC_REDIRECT_URI` | 223 | `http://127.0.0.1:5173/callback` | 生产 HTTPS 回调 |
| `OIDC_DISCOVERY_TIMEOUT` | 224 | `3.0` | `3.0` |
| `OIDC_USER_CLAIM` | 227 | `preferred_username` | 同 |
| `OIDC_DEPT_CLAIM` | 229 | `affiliation` | 同 |
| `OIDC_ROLE_CLAIM` | 232 | `role` | 同 |
| `OIDC_TENANT_CLAIM` | 234 | `""` | `""` |
| `OIDC_NAME_CLAIM` | 235 | `name` | 同 |
| `AUTH_ENFORCE_OIDC` | 269/270 | dev 下 `False` | 阶段1 `0` → 阶段2 `1` |

注：验签走 **RS256 + JWKS 发现**（`{issuer}/.well-known/openid-configuration`），**不需要 client secret 变量**；若日后后端要做授权码换码（code exchange），需另行在 Casdoor 侧取应用 Client Secret 并补一个 secret 变量（当前代码未用到）。

---

## 4. 核验命令（阶段 1 完成后跑）

```bash
# 1) OIDC 发现可达 + 配置齐全
curl -s https://ai.your-domain.com/api/v1/auth/oidc/health

# 2) 用浏览器走完整 SSO 登录，确认能拿到 token 且可调用受保护接口
#    （前端 Login.tsx 在 enforce_oidc=true 时自动隐藏演示入口，无需改前端代码）

# 3) 收口前，dev_admin 仍应可用（阶段 1 未收口）
curl -s -X POST https://ai.your-domain.com/api/v1/auth/sso/callback \
  -H 'content-type: application/json' -d '{"provider":"wecom","code":"dev_admin","device_id":"mp"}'
# → 应返回 200 与 token
```

## 5. 阶段 2 翻开关后回归

```bash
# dev_admin 必须被拒（返回 401/403，不再发 token）
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://ai.your-domain.com/api/v1/auth/sso/callback \
  -H 'content-type: application/json' -d '{"provider":"wecom","code":"dev_admin","device_id":"mp"}'
# → 期望 401/403

# 真实 SSO 登录仍 200
```

## 6. 回滚
- 阶段 1 出问题：`OIDC_ENABLED=0`（或整段注释），`AUTH_ENFORCE_OIDC` 保持 `0` → 回到 dev 后门可用态。
- 阶段 2 出问题：仅把 `AUTH_ENFORCE_OIDC` 改回 `0`（OIDC 仍启用），即可恢复 dev 后门可用，无需动 Casdoor。
- 兜底：保留本次改动前的 `.env` 备份（建议 `cp .env .env.bak.$(date +%s)`）。
