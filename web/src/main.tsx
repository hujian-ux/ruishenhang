import React from "react";
import ReactDOM from "react-dom/client";
import { Root } from "./Root";
import { getSession } from "./auth/session";
import { setToken } from "./api/client";

// 启动前注入登录态 token（整页刷新后仍能带 Authorization），再由 Root 分流。
const session = getSession();
if (session) setToken(session.idToken);

// PWA：仅生产构建注册 Service Worker（开发态不注册，避免缓存干扰 HMR）
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
