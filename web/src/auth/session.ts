// 登录态：id_token 存 localStorage，作为 Authorization 头发给后端（由网关验签）。

export interface SessionUser {
  userId: string;
  name: string;
  deptId: string;
  role: string;
}

export interface Session {
  idToken: string;
  user: SessionUser;
  dev: boolean; // true = 开发模式 dev token（非 OIDC）
}

const KEY = "rsp_session_v1";

export function setSession(s: Session): void {
  localStorage.setItem(KEY, JSON.stringify(s));
}

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (!s || !s.idToken) return null;
    return s;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  localStorage.removeItem(KEY);
}

// 登录态是否已过期。前端也必须判：只靠后端 401 的话，用户会先看到一屏
// "加载失败" 才知道自己掉线了（M21 就是这么被发现的）。
// 两种 token 都解：dev token 是 `dev base64url(json).sig`，OIDC 是标准 JWT。
export function sessionExpired(s: Session, skewSec = 60): boolean {
  try {
    const raw = s.idToken.startsWith("dev ")
      ? s.idToken.slice(4).split(".")[0]
      : s.idToken.split(".")[1];
    if (!raw) return false; // 解不出来就别拦，交给后端判
    const pad = raw.length % 4 ? "=".repeat(4 - (raw.length % 4)) : "";
    const payload = JSON.parse(atob(raw.replace(/-/g, "+").replace(/_/g, "/") + pad));
    const exp = Number(payload.exp || 0);
    if (!exp) return false;
    return exp - skewSec <= Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}
