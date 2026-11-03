import { App } from "./App";
import { LoginPage } from "./pages/Login";
import { CallbackPage } from "./pages/Callback";
import { ConnectSetupPage } from "./pages/ConnectSetup";
import { clearSession, getSession } from "./auth/session";

// 轻量路由（无第三方依赖）：/callback 换码、/setup 连接配置、其余按登录态分流。
export function Root() {
  const path = window.location.pathname;
  if (path === "/callback") return <CallbackPage />;
  if (path === "/setup") return <ConnectSetupPage />;

  const session = getSession();
  if (session) {
    return (
      <App
        session={session}
        onLogout={() => {
          clearSession();
          window.location.href = "/";
        }}
      />
    );
  }
  return <LoginPage />;
}
