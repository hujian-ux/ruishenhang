# 身份与 IM 部署与生效手册（P1-173）

> 适用对象：运维 / 管理员（含零基础）。
> 目标：把「统一身份登录（Casdoor/OIDC）」和「企业微信 / 钉钉 消息接入」从代码变成可上线能力。
> 原则：所有默认值都关、零行为变更；本手册只说"怎么开"，不谎称已自动生效。

---

## 0. 先弄清三件事（避免拿错凭据）

| 东西 | 是什么 | 谁来用 | 拿错的常见坑 |
|---|---|---|---|
| **OIDC / Casdoor** | 企业的"统一登录后台"，员工用企业账号登进来 | 登录页跳转、身份收口 | 把企微/钉钉的 CorpID 当 Issuer 填——错 |
| **企业微信 / 钉钉 接入** | 让员工在 IM 里收消息、点审批 | 本系统的"消息下发 / 审批回推" | 和上面的"身份登录"是两回事，凭据不通用 |
| **AUTH_ENFORCE_OIDC** | 收口开关：开了就**禁止演示账号**，只能走真实 SSO | 安全合规 | 没配好身份源就开 → 所有人都登不进来 |

> 一句话：OIDC 管"人怎么登进来"，企微/钉钉管"系统怎么给人发消息"。两套凭据分开拿、分开填。

---

## 1. 如何获取 Casdoor（统一身份源）凭据 —— 小白版

### 1.1 什么是 Casdoor
开源的统一身份网关。你可以把它理解成"公司内部的微信登录后台"。我们系统不内置账号体系，而是信任 Casdoor 发的登录凭证（id_token）。

### 1.2 部署 Casdoor（二选一）
- **最快（Docker 一行）**：
  ```bash
  docker run -d -p 8000:8000 --name casdoor casbin/casdoor:latest
  ```
  浏览器开 `http://<你的服务器IP>:8000`，默认管理员 `admin` / `123`，**第一次登录务必改密码**。
- **已有 Casdoor**：跳过部署，直接用现成的地址。

### 1.3 拿凭据（3 步）
1. 左侧 **Applications → 新建应用**，名字随便（如 `ruishen-ai`）。
2. 建好后，应用详情里有 **Client ID**（复制下来）和 **Client Secret**（可选，本系统目前只用到 ID）。
3. 同一页把 **Redirect URLs** 加上：`http://<前端访问地址>/callback`
   - 本机联调：`http://localhost:5173/callback`
   - 上线后：`https://你的域名/callback`

### 1.4 你最终拿到的是
- **Issuer** = Casdoor 的访问地址，如 `http://127.0.0.1:8000`（必须和浏览器访问地址**完全一致**，差一个字符都验签失败）
- **Client ID** = 上面复制的那串

### 1.5 填到本系统
- 前端：登录页点「配置身份源」，填 Issuer + Client ID → 保存。
- 后端：环境变量 `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_AUDIENCE`（见第 4 节）。

---

## 2. 如何获取企业微信凭据 —— 小白版

### 2.1 前提
- 注册企业微信：`https://work.weixin.qq.com`（个人也能建企业，免费）。
- 进入**管理员后台**。

### 2.2 拿 CorpID
**我的企业 → 企业信息 → 最下方 CorpID**（一串英文数字），复制。

### 2.3 建"自建应用"拿 AgentId + Secret
1. **应用管理 → 自建 → 创建应用**，填名称/logo，可见范围选"全员"或指定部门。
2. 建好进入应用详情：
   - **AgentId**：应用概览里直接看到。
   - **Secret**：点"查看" → 用企业微信扫码或短信验证后显示，复制（只显示一次）。

### 2.4 配"接收消息"回调（发消息 / 回推审批必须）
1. 应用详情 → **接收消息 → 设置 API 接收**。
2. **URL** 填：`https://<你的后端域名>/api/v1/integrations/im/wecom/callback`
3. **Token** 和 **EncodingAESKey**：页面会让你自己设（或随机生成），**把它们记下来**——这就是本系统 IM 配置里的 `webhook_token` 和 `encoding_aes_key`。
   > 没配 EncodingAESKey 也能验签，但收到的消息体不解密（本系统会留占位提示）。

### 2.5 你最终拿到的是
`CorpID`、`CorpSecret`、`AgentId`、回调 `Token`、`EncodingAESKey`（可选）。

---

## 3. 如何获取钉钉凭据 —— 小白版

### 3.1 前提
- 钉钉开放平台：`https://open.dingtalk.com`
- 登录后进入**开发者后台**。

### 3.2 建企业内部应用拿 AppKey / AppSecret
1. **应用开发 → 企业内部开发 → 创建应用**，填名称。
2. 应用详情：**AppKey**（即 Client ID）、**AppSecret**（点查看复制）。

### 3.3 配事件订阅回调（发消息必须）
1. 应用详情 → **事件订阅**。
2. **请求地址** 填：`https://<你的后端域名>/api/v1/integrations/im/dingtalk/callback`
3. **加签密钥**：自己设一个字符串（页面叫"签名密钥"），**记下来**——这就是本系统 IM 配置里的 `webhook_token`（钉钉侧叫加签密钥）。

### 3.4 你最终拿到的是
`AppKey`、`AppSecret`、事件订阅的**加签密钥**。

---

## 4. 后端环境变量（改完重启后端）

```bash
# ── 统一身份（OIDC / Casdoor）──
OIDC_ENABLED=1
OIDC_ISSUER=http://127.0.0.1:8000          # 与 Casdoor 访问地址完全一致
OIDC_CLIENT_ID=<Client ID>
OIDC_AUDIENCE=<Client ID>                    # 多数情况与 CLIENT_ID 相同
OIDC_REDIRECT_URI=https://你的域名/callback  # 不填则取前端当前地址

# ── 身份收口（开了就禁演示账号，必须走真实 SSO）──
# 仅在"身份源已验证可登录"之后再开！否则全员登不进来。
AUTH_ENFORCE_OIDC=0

# ── 联网搜索（之前已配，列在这里提醒别漏）──
WEB_SEARCH_DECOMPOSE=0     # 搜索词三轮拆解，按需开
FEWSHOT_ENABLED=0          # 166 样例注入，按需开
```

IM（企微/钉钉）的 CorpID/Secret/AgentId/Token 等**不在环境变量里**，而是在前端「身份与集成」管理页保存（存后端内存，列表脱敏）。

---

## 5. 前端两页怎么用

### 5.1 配置身份源（`/setup`，所有人可见）
- 填 **Issuer + Client ID** → 保存 → 登录页出现「使用企业身份登录」。
- 还能"复制 Casdoor 填写清单""一键写入 Casdoor"（实验性，失败就用清单手动填）。

### 5.2 身份与集成（`/admin` → 身份与集成 tab，**仅平台管理员**）
- **① 统一身份**：展示 OIDC 是否开启、收口是否开启、脱敏后的 Issuer/Client ID；带"配置身份源""查 OIDC 健康"按钮。
- **② 消息接入（运行时）**：选 企微/钉钉，填 CorpID/Secret/AgentId/回调Token/AESKey → 保存（密钥不回显）。
- **③ 已接入列表与测试**：看已存配置（脱敏）、删除；填"接收人+文本"可**真实下发测试消息**或**测试审批回推**。

---

## 6. 推荐开启顺序（含回滚）

| 步骤 | 动作 | 验证 | 回滚 |
|---|---|---|---|
| 1 | 部署/确认 Casdoor 可达，`OIDC_ENABLED=1` + 填好 Issuer/ClientID | 登录页出现企业身份入口，能跳 Casdoor 并登录回首页 | 把 `OIDC_ENABLED` 置 0 |
| 2 | 前端「配置身份源」填同样 Issuer/ClientID | 同一账号可登录 | 清前端 localStorage 配置 |
| 3 | 管理员在「身份与集成」保存企微/钉钉接入 | 用"测试下发消息"收到真实消息 | 删掉该接入配置 |
| 4 | **最后**才把 `AUTH_ENFORCE_OIDC=1` | 演示入口消失、只能走 SSO | 立刻置 0 重启（若误开导致登不进） |

> 关键：**第 4 步务必在第 1–2 步验证可登录之后再开**。收口是"硬开关"，开错会让演示账号也失效。

---

## 7. 诚实标注（当前未实测 / 待补）

- **企微消息体 AES 解密**：需要 `EncodingAESKey` + `pycryptodome`。当前代码已留清晰占位，未配则不解密、不谎称已收。装上依赖、填好 EncodingAESKey 即生效。
- **IM 真收发**需要后端服务器能访问企微/钉钉网关（公网或白名单）。
- **Casdoor 一键写入**是实验性功能，失败请用手册 1.5 的清单手动填（保证可用）。
- **前端登录页自动跳 SSO**仅在"已配身份源 + 收口开启"时触发；首次配置请先手动点「使用企业身份登录」确认链路通。

---

## 8. 排错速查

- 登录后 401 / "无 caller context"：token 没带 → 清 localStorage 重登；或 Casdoor 与后端 `OIDC_ISSUER` 不一致。
- 验签失败：issuer 末尾别带 `/`；`OIDC_AUDIENCE` 必须等于 Casdoor 应用 Client ID。
- 回调 401：企微/钉钉的 Token/加签密钥与「身份与集成」页填的不一致。
- 收口后登不进：先确认 Casdoor 应用 Redirect URLs 含 `/callback`，再确认 `OIDC_ENABLED=1` + Issuer/ClientID 正确。
