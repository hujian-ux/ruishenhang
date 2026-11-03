// 连接配置（自助接入）：存 localStorage，Setup 页可改，登录/换码读取。
// issuer + clientId 决定登录能否走通；wecom/dingtalk 是给 Casdoor 后台填的原始资料。

export interface WeComConfig {
  corpId: string;   // 企业微信 CorpID
  secret: string;   // 自建应用 Secret（CorpSecret）
  agentId: string;  // 自建应用 AgentId
}

export interface DingTalkConfig {
  appKey: string;    // 钉钉 AppKey / Client ID
  appSecret: string; // 钉钉 AppSecret / Client Secret
}

export interface OidcConfig {
  issuer: string;
  clientId: string;
  wecom: WeComConfig;
  dingtalk: DingTalkConfig;
}

const KEY = "rsp_oidc_config_v1";

// 占位 issuer：本地 Casdoor 默认地址；等填 Client ID 即可直接用。
export const PLACEHOLDER_ISSUER = "http://127.0.0.1:8000";

export function defaultConfig(): OidcConfig {
  return {
    issuer: PLACEHOLDER_ISSUER,
    clientId: "",
    wecom: { corpId: "", secret: "", agentId: "" },
    dingtalk: { appKey: "", appSecret: "" },
  };
}

export function loadConfig(): OidcConfig {
  const d = defaultConfig();
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return d;
    const c = JSON.parse(raw) as Partial<OidcConfig>;
    return {
      issuer: c.issuer ?? d.issuer,
      clientId: c.clientId ?? d.clientId,
      wecom: { ...d.wecom, ...(c.wecom ?? {}) },
      dingtalk: { ...d.dingtalk, ...(c.dingtalk ?? {}) },
    };
  } catch {
    return d;
  }
}

export function saveConfig(c: OidcConfig): void {
  localStorage.setItem(KEY, JSON.stringify(c));
}

export function isConfigured(c: OidcConfig): boolean {
  return !!c.issuer && !!c.clientId;
}

// 回调地址：当前站点 origin + /callback（须与 Casdoor 应用 Redirect URLs 一致）。
export function redirectUri(): string {
  return `${window.location.origin}/callback`;
}
