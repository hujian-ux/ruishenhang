import { useEffect, useState } from "react";
import { App } from "./App";
import { LoginPage } from "./pages/Login";
import { CallbackPage } from "./pages/Callback";
import { ConnectSetupPage } from "./pages/ConnectSetup";
import { clearSession, getSession, sessionExpired } from "./auth/session";

// 轻量路由（无第三方依赖）：/callback 换码、/setup 连接配置、其余按登录态分流。
export function Root() {
  // 掉线计数：收到 401 时清会话并重渲染到登录页（不用整页刷新）
  const [kicked, setKicked] = useState(0);

  useEffect(() => {
    const onOut = () => {
      clearSession();
      setKicked((k) => k + 1);
    };
    window.addEventListener("wb-unauthorized", onOut);
    return () => window.removeEventListener("wb-unauthorized", onOut);
  }, []);

  const path = window.location.pathname;
  if (path === "/callback") return <CallbackPage />;
  if (path === "/setup") return <ConnectSetupPage />;

  const session = getSession();
  const expired = !!session && sessionExpired(session);

  // token 过期就别放进去：否则用户先在技能库/对话上撞一屏"加载失败"，
  // 再猜是自己点坏了还是系统坏了。
  // 注意别在这个分支里 clearSession()：render 期写 localStorage 会被 StrictMode
  // 的二次挂载吃掉，提示语当场变空；过期会话留着也无害，登录成功会覆盖它。
  if (expired) {
    return <LoginPage notice="登录已过期（演示账号有效期 12 小时），请重新登录。" />;
  }

  if (session) {
    return (
      <App
        key={kicked}
        session={session}
        onLogout={() => {
          clearSession();
          window.location.href = "/";
        }}
      />
    );
  }
  return <LoginPage notice={kicked ? "登录状态已失效，请重新登录。" : ""} />;
}
