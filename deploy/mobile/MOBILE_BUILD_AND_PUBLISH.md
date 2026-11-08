# 原生移动端（iOS / 安卓）构建与上架指南

> 现状：前端（React/Vite）已做好响应式适配（P1 基线，`npm run build` 通过，规则进 bundle），
> Capacitor 脚手架已就位（`web/capacitor.config.ts`）。本指南补齐「把 Web 包成原生 App 并上架」的全流程。
>
> ⚠️ **本机（Windows）编不了原生包**：iOS 必须 macOS + Xcode，安卓需 Android Studio/SDK。
> 本环境可完成的部分：前端改 API base、Web 构建、Android 资源同步（`cap sync` 的 Web 侧）。
> 真机编译 / 签名 / 提交商店需在你那边的 Mac（iOS）或装了 Studio 的机器（安卓）执行。

---

## 0. 前置条件

| 平台 | 必需 | 费用 |
|------|------|------|
| iOS | macOS + Xcode（App Store 版）+ Apple Developer 账号 | $99/年（个人/组织） |
| 安卓 | Android Studio + Android SDK（Windows/macOS/Linux 均可） | 一次性 $25 注册 Google Play 开发者 |

通用：`Node.js ≥ 18`、`@capacitor/cli`、`npm`。

---

## 1. 让原生包连上「部署后的后端」（关键一步）

Web 开发时 API 走相对路径 `/api/v1`。原生包跑在手机上，必须指向真实后端地址。

两种注入方式（二选一）：

**(A) 编译时环境变量（推荐）**
```bash
# web/.env.production
VITE_API_BASE=https://ai.your-domain.com/api/v1
```
构建时 Vite 会把 `import.meta.env.VITE_API_BASE` 写死进包。代码见 `web/src/api/client.ts`。

**(B) Capacitor 原生壳 server.url（覆盖方式）**
编辑 `web/capacitor.config.ts`：
```ts
server: { androidScheme: 'https', url: 'https://ai.your-domain.com' }
```
> 注意：用了 (B) 后，原生壳会直接加载远端 Web，**不再打包本地 dist**。调试用，上架前通常改回 (A)。

---

## 2. 构建 Web 产物

```bash
cd web
npm install
npm run build          # 输出到 web/dist（tsc 类型检查 + vite 打包）
```

---

## 3. 添加原生平台并同步

```bash
cd web
# 安装 Capacitor CLI（若未装）
npm install -D @capacitor/cli @capacitor/core
npm install @capacitor/ios @capacitor/android

# 初始化（首次；appId/ appName 见 capacitor.config.ts，可改）
npx cap init

# 添加平台（iOS 仅 macOS 可加）
npx cap add android
npx cap add ios        # 需要 macOS + Xcode

# 把最新 web/dist 同步进原生工程（每次改前端都要跑）
npx cap sync
```

> 本 Windows 环境能跑 `npx cap add android` + `npx cap sync` 的**资源侧**（把 dist 拷进安卓工程），
> 但最终 `assembleRelease` / Xcode archive 仍需对应 OS 与 IDE。

---

## 4. 各平台上架

### 4.1 安卓（Google Play）
1. Android Studio 打开 `web/android`。
2. 生成上传密钥库（**务必离线备份**）：
   ```bash
   keytool -genkeypair -v -keystore upload-key.keystore -alias upload \
     -keyalg RSA -keysize 2048 -validity 10000
   ```
3. 在 `android/app/build.gradle` 配置 `signingConfigs.release`（指向上面的 keystore）。
4. `Build → Generate Signed Bundle / APK → Android App Bundle (AAB)`。
5. Play Console 新建应用，填商店资料（见 `store-listing/google-play.md`），上传 AAB，走内测 → 生产发布。

### 4.2 iOS（App Store）
1. macOS 上 Xcode 打开 `web/ios/App`。2. 配置 Signing（Team + Bundle Identifier `com.ruishenxing.aiworkbench`，见 config）。
3. 修图标/启动屏（`npx cap copy` 或 Xcode 资产目录；图标用 `npx cap add` 后的 `assets`）。
4. `Product → Archive → Distribute App → App Store Connect`。
5. App Store Connect 填资料（见 `store-listing/app-store.md`），提交审核。

---

## 5. 图标 / 启动屏

- 用 Capacitor 官方工具生成多尺寸图标：
  ```bash
  npm install -D @capacitor/assets
  npx cap assets generate   # 读取 web/assets/icon.png + splash.png，产出各平台尺寸
  ```
- 源图建议 ≥ 1024×1024（图标）、2732×2732（启动屏）。

---

## 6. 商店资料 / 合规

- 见 `store-listing/`：`app-store.md`、`google-play.md`、`privacy-policy.md`、`screenshots-spec.md`、`keywords.md`。
- 隐私政策 URL 必填（双平台），模板在 `privacy-policy.md`，需托管到可公网访问地址后填入。

---

## 7. 灰度与更新

- 原生包更新比 Web 慢（要走商店审核）。紧急前端修复优先走「后端 + Web 热更」；原生壳只在大版本更。
- 可用 Capacitor 的 `CapacitorUpdater`（第三方）做 Web 层热更新，绕过商店审核——按需引入。

---

## 诚实边界

- 本环境（Windows）**未执行**真机编译 / 签名 / 上架，上述为可执行步骤与配置，**非已上架状态**。
- API base 切换、响应式前端、Capacitor 配置已就绪且可构建验证；物理打包与商店提审需你侧 Mac/Studio 完成。
