# 前端部署指南（网页版 / PWA）

本目录是「睿深行 AI 工作台」的 Web 前端（React + Vite + TypeScript），已具备移动端响应式
（≤860px 抽屉式侧栏、单列布局）与 PWA（可"加到主屏"、离线壳、主题色）。

## 1. 构建
```bash
cd web
npm install            # 首次或依赖变更时
npm run build          # 产物输出到 web/dist/
```
`dist/` 内含 `index.html`、`assets/`（带 contenthash 的 JS/CSS）、`manifest.webmanifest`、
`sw.js`、`icon.svg`、`icon-maskable.svg`——均由 Vite 从 `public/` 自动拷入。

## 2. 环境变量（构建时注入）
复制 `.env.example` 为 `.env`（自托管）或 `.env.production`（原生包）后填写：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `VITE_API_BASE` | 后端基址，**须含 `/api/v1` 后缀**。同源部署（经反代）留空走相对路径；跨域/原生包须填 `https://域名/api/v1` | 空（相对 `/api/v1`） |

```bash
# 生产原生包 / 跨域示例
VITE_API_BASE=https://ai.your-domain.com/api/v1
```

## 3. 托管（关键：SPA 回退）
`dist/` 是单页应用，**所有深链接刷新都要回退到 `index.html`**，否则 404。
参考 [`nginx.conf.example`](./nginx.conf.example)：核心一行 `try_files $uri $uri/ /index.html;`。
其它静态服务器（Caddy / Vercel / Nginx / 对象存储+CDN）同理配置 history fallback。

## 4. HTTPS（强制）
PWA 安装与微信小程序 `request` 合法域名都**要求 HTTPS**。
- 证书：Certbot（Let's Encrypt）免费；或云厂商 DV 证书。
- 配置见 `nginx.conf.example` 的 443 server 段与 80→443 跳转。

## 5. 同源还是跨域？
- **同源（推荐）**：前端与后端同一域名，前端留空 `VITE_API_BASE`，用 Nginx 把 `/api` 反代到后端
  （见 `nginx.conf.example` 注释段）。无 CORS 烦恼，PWA/小程序都最省事。
- **跨域**：前端填 `VITE_API_BASE=https://api.域名/api/v1`；后端需放开 CORS 且该域名在白名单。

## 6. PWA 验证
- 桌面 Chrome / Edge：DevTools → Application → Manifest / Service Workers 检查无报错；
  Lighthouse → PWA 项应全绿。
- 手机：浏览器打开站点 → "添加到主屏幕" → 图标出现在桌面，点开即全屏独立运行。
- 离线：断网后从主屏图标进入，应能看到缓存的 app 壳（对话等需联网的功能会提示网络不可用）。

## 7. 小程序的前置依赖（重要）
微信小程序复用同一后端 API，**上线前必须**：
1. 后端已部署到**公网 HTTPS**（本站点或独立 API 域名均可）；
2. 在微信公众平台 → 小程序 → 开发管理 → 开发设置 → 「服务器域名」中，
   把 `request` 合法域名填上后端 HTTPS 域名（仅支持 https，且需 ICP 备案）。
详见 `deploy/mobile/MOBILE_BUILD_AND_PUBLISH.md`。

## 8. 进阶（可选）
- 想更"原生"：当前已带 Capacitor 脚手架（`capacitor.config.ts`），可在 Mac/Android Studio
  侧 `npm run build && npx cap add ios/android && npx cap sync` 打包原生壳（仍走同一 Web 代码）。
- 图标如需更高保真（尤其 iOS/商店），可将 `public/icon.svg` 导出 192×192 与 512×512 的 PNG
  替换 `manifest.webmanifest` 的 icons（当前 SVG 已可在 Android/Chrome 安装）。
