# 后端生产部署指南（公网 HTTPS）

目标：把「睿深行 AI 工作台」后端（FastAPI）+ 前端（dist）部署到一台有公网 IP 的主机，
通过 Caddy 自动申请 Let's Encrypt 证书，单源域名同时提供 API 与静态站点（**免 CORS**，
对 PWA 安装与微信小程序最友好）。

## 架构
```
用户浏览器 / 微信小程序
        │  https://ai.你的域名.com
        ▼
   Caddy（:80/:443，自动 TLS）
    ├── /            → 静态 dist（SPA，try_files 回退 index.html）
    ├── /api/*       → backend:8080（保留 /api 前缀，同域免 CORS）
    └── /healthz     → backend:8080
        ▼
   backend（FastAPI，内存模式或接 PG/Qdrant/MinIO/Redis）
```

## 前置
1. 一台 Linux 主机（≥2 vCPU / 4 GB 即可演示），有公网 IP。
2. 一个已备案域名（国内小程序 request 域名强制 ICP 备案），A 记录指向该 IP；开放 80/443 入站。
3. 已装 Docker Engine + Docker Compose v2。
4. 已构建前端：`cd web && npm install && npm run build`（产物 `web/dist/`）。

## 部署步骤
```bash
# 1) 准备环境变量
cp deploy/.env.prod.example .env
#   编辑 .env：填 SITE_DOMAIN、CADDY_EMAIL，确认 AUTH_ENFORCE_OIDC=1

# 2) 拉起（基础 compose 的底座依赖 + app profile 下的 backend + caddy）
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile app up -d

# 3) 观察
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose logs -f caddy          # 看证书申请是否成功
```

## 验证
- 健康检查：`curl https://你的域名/healthz` → `{"status":"ok",...}`（200）。
- 站点：浏览器打开 `https://你的域名/` → 应加载 PWA 工作台；DevTools → Application → Manifest/PWA 无报错。
- 登录：生产态 `AUTH_ENFORCE_OIDC=1` 已禁用 `dev_admin`，需走真实 SSO（Casdoor）。
  未接 SSO 前无法登录——上线前务必完成 Casdoor 对接（见基础设施文档）。

## 持久化（生产必做）
默认后端是**内存模式**，重启丢数据。要持久化，在 `.env` 取消注释 `POSTGRES_DSN` 等，
并编辑 `docker-compose.yml` 中 `backend` 的 `depends_on` 与 `environment` 注释块，重新 `up -d`。

## 微信小程序上线前置（重要）
1. 后端已在**公网 HTTPS**（本方案满足）。
2. 微信公众平台 → 小程序 → 开发 → 开发设置 → 服务器域名 → `request` 合法域名填 `https://你的域名`
   （仅支持 https，且域名需 ICP 备案）。
3. 开发调试：微信开发者工具勾选「不校验合法域名、TLS 版本以及 HTTPS 证书」可先用临时隧道测。

## 安全收口（生产红线）
- `AUTH_ENFORCE_OIDC=1` 必须开：否则 `dev_admin` 后门可被任何人换平台管理员令牌。
- 接真实企业 SSO（Casdoor）后再对外开放；不要带 dev 后门暴露公网。
- 证书数据在 `caddy_data` 卷，勿删，避免频繁申请被 Let's Encrypt 限流。

## 临时测试（不走本方案）：cloudflared 隧道
本地开发想让手机/小程序即时访问，可不开整机部署，用 cloudflared 临时隧道：
```bash
# 终端1：本地起后端（内存模式）
cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
# 终端2：开隧道（会打印 https://xxx.trycloudflare.com）
cloudflared tunnel --url http://127.0.0.1:8080
```
⚠️ 隧道是**会话级临时**、域名每次变化、且若未设 `AUTH_ENFORCE_OIDC=1` 会暴露 `dev_admin` 后门；
仅用于个人快捷测试，测试完即杀，切勿作生产。
