import React from "react";
import ReactDOM from "react-dom/client";
import { Root } from "./Root";
import { getSession } from "./auth/session";
import { setToken } from "./api/client";

// 启动前注入登录态 token（整页刷新后仍能带 Authorization），再由 Root 分流。
const session = getSession();
if (session) setToken(session.idToken);

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
