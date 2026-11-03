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
