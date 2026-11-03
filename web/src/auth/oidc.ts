// 标准 OIDC Authorization Code + PKCE（S256）的浏览器侧实现。
// 只负责「拼授权 URL / 生成 PKCE / 解 id_token 取展示信息」；换码放到后端（避开 CORS）。

export function randomString(len = 64): string {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
  const arr = new Uint8Array(len);
  crypto.getRandomValues(arr);
  let out = "";
  for (let i = 0; i < arr.length; i++) out += chars[arr[i] % chars.length];
  return out;
}

function base64Url(bytes: Uint8Array): string {
  let str = "";
  for (let i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return base64Url(new Uint8Array(digest));
}

// Casdoor 授权端点：{issuer}/login/oauth/authorize
export function buildAuthorizeUrl(
  issuer: string,
  clientId: string,
  redirect: string,
  state: string,
  challenge: string,
): string {
  const base = issuer.replace(/\/+$/, "");
  const u = new URL(`${base}/login/oauth/authorize`);
  u.searchParams.set("client_id", clientId);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("scope", "openid profile email");
  u.searchParams.set("redirect_uri", redirect);
  u.searchParams.set("state", state);
  u.searchParams.set("code_challenge_method", "S256");
  u.searchParams.set("code_challenge", challenge);
  return u.toString();
}

export function decodeJwtPayload(token: string): Record<string, any> {
  try {
    const part = token.split(".")[1] || "";
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const pad = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const bin = atob(pad);
    let json = "";
    for (let i = 0; i < bin.length; i++) {
      json += "%" + ("00" + bin.charCodeAt(i).toString(16)).slice(-2);
    }
    return JSON.parse(decodeURIComponent(json)) as Record<string, any>;
  } catch {
    return {};
  }
}

export interface DisplayUser {
  userId: string;
  name: string;
  deptId: string;
  role: string;
}

// 从 id_token 取展示用身份（后端验签为准，这里仅用于界面显示）。
export function userFromIdToken(idToken: string): DisplayUser {
  const p = decodeJwtPayload(idToken);
  const userId = String(p.preferred_username || p.email || p.sub || "user");
  const name = String(p.name || p.displayName || p.preferred_username || userId);
  const deptRaw = p.affiliation ?? p.dept ?? p.organization ?? "";
  const deptId = Array.isArray(deptRaw) ? String(deptRaw[0] ?? "") : String(deptRaw || "");
  let role = "employee";
  if (p.isGlobalAdmin) role = "platform_admin";
  else if (p.isAdmin) role = "dept_admin";
  else if (
    typeof p.role === "string" &&
    ["employee", "dept_admin", "platform_admin"].includes(p.role)
  )
    role = p.role;
  return { userId, name, deptId, role };
}
