import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发服务器：代理 /api 到后端单体（:8080），避免跨域
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // 显式 IPv4 绑定，避免 localhost 解析到 ::1 导致本机/代理连不上
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/healthz": "http://127.0.0.1:8080",
    },
  },
});
