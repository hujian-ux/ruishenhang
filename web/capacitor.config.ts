import type { CapacitorConfig } from '@capacitor/cli';

// 企业 AI 智能体中台 · 移动端（Capacitor 包壳）
// 思路：复用现有 React/Vite 前端，Capacitor 把它包成 iOS/安卓原生 WebView 壳，
// 不是用 Swift/Kotlin 重写。改动只在这一层 + 前端的响应式适配（见移动端 P1）。
const config: CapacitorConfig = {
  // 上架时的包名（按你们域名反转，可改）
  appId: 'com.ruishenxing.aiworkbench',
  appName: '睿深行AI工作台',
  // Vite 构建产物目录（package.json 的 build 脚本输出到 dist）
  webDir: 'dist',
  // 原生壳里用 https scheme，避免 Android 明文流量限制
  server: {
    androidScheme: 'https',
    // 注意：原生包连的是「部署后的后端地址」，不是 localhost。
    // 真机联调时把后端 URL 填这里（如 https://ai.your-domain.com）；
    // 留空则走前端编译时注入的 API base（见 src/api 的 VITE_API_BASE）。
    // url: 'https://your-backend.example.com',
  },
  // 真机/模拟器联调时可让原生壳加载本地 dev server（调试用，上架前删）：
  // cli: { podPath: './ios/App' },
};

export default config;
