# 睿深行 AI 工作台 · 微信小程序

与 Web/H5 共用同一套后端 API（`/api/v1`），零业务代码重复。本目录是**可直接导入微信开发者工具**的工程骨架。

## 1. 导入与基础配置
1. 打开「微信开发者工具」→ 导入项目，目录选本 `miniprogram/`，AppID 填你的小程序 AppID（或用测试号 `touristappid` 先预览）。
2. 改 `utils/api.js` 的 `API_BASE` 为你的**后端 HTTPS 域名**（如 `https://ai.your-domain.com`）。
3. 小程序后台「开发管理 → 开发设置 → 服务器域名」把该 HTTPS 域名加入 **request 合法域名**（真机预览强制校验；本地开发者工具勾选「不校验合法域名」可临时跳过）。

## 2. 登录（接同一身份）
- 演示态（`AUTH_ENFORCE_OIDC=0`）：登录页提供「员工/管理员视角」走 `dev`/`dev_admin` 捷径，与 Web 一致。
- 收口态（`AUTH_ENFORCE_OIDC=1`）：演示入口自动隐藏，引导走企业 SSO。
- 生产真机登录：小程序无法像 Web 那样做 OIDC 重定向跳转，标准做法是
  `wx.login()` 拿 `code` → 后端用微信 `code2session` 换 openid → 映射租户用户 → 签发平台 token。
  该 wx 登录桥接需后端新增 `/auth/wx/login` 端点（含微信 AppSecret），本骨架预留 `utils/session.js`
  与 `app.globalData.token` 透传，接入时只需补该端点并在 `pages/login/login.js` 的 `enforce` 分支调用。

## 3. 页面
- `pages/login` 登录（探测收口态、dev/SSO 双路）。
- `pages/chat` 选技能 → 建会话 → 发消息 → 看回复（接 `/agents`、`/sessions`）。
- `pages/outputs` 列出本会话产出物（xlsx/docx/pptx/pdf）并复制下载链接。

## 4. 与后端契约
小程序调用的路径与 Web `web/src/api/client.ts` 同源（`/auth/settings`、`/auth/sso/callback`、
`/agents`、`/sessions`、`/sessions/{id}/messages`、`/sessions/{id}/outputs`）。
字段名以 `backend/openapi.yaml` 为准；本骨架对响应做了容错解析（`data.items / data.agents / data` 多形态兼容）。

## 5. 上架前置（参考 web/DEPLOY.md）
- 已备案域名 + HTTPS 后端（见 `deploy/Caddyfile`、`deploy/BACKEND_DEPLOY.md`）。
- request 合法域名白名单、业务域名（如需 web-view 打开产出物）。
- 隐私协议与《小程序服务条款》合规。
