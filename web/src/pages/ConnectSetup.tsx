import { useMemo, useState } from "react";
import { api } from "../api/client";
import { AUTH_STYLE, STYLE } from "../theme";
import { isConfigured, loadConfig, redirectUri, saveConfig, type OidcConfig } from "../auth/config";

// 自助连接页：填 issuer + Client ID 即可让「企业身份登录」生效；
// 企业微信/钉钉资料用于写入 Casdoor（可复制清单手动填，或一键写入）。
export function ConnectSetupPage() {
  const [cfg, setCfg] = useState<OidcConfig>(loadConfig());
  const [saved, setSaved] = useState("");
  const [copied, setCopied] = useState(false);

  // 一键写入 Casdoor（可选）
  const [adminUser, setAdminUser] = useState("admin");
  const [adminPass, setAdminPass] = useState("");
  const [appName, setAppName] = useState("app-built-in");
  const [prov, setProv] = useState<any>(null);
  const [provBusy, setProvBusy] = useState(false);

  const set = (patch: Partial<OidcConfig>) => setCfg((c) => ({ ...c, ...patch }));
  const setWecom = (patch: Partial<OidcConfig["wecom"]>) =>
    setCfg((c) => ({ ...c, wecom: { ...c.wecom, ...patch } }));
  const setDing = (patch: Partial<OidcConfig["dingtalk"]>) =>
    setCfg((c) => ({ ...c, dingtalk: { ...c.dingtalk, ...patch } }));

  const doSave = () => {
    saveConfig(cfg);
    setSaved(`已保存。当前连接：${isConfigured(cfg) ? "已具备登录条件" : "仍缺 Client ID"}`);
  };

  const guide = useMemo(() => {
    const w = cfg.wecom;
    const d = cfg.dingtalk;
    return [
      "【Casdoor 后台填写清单】",
      `· 回调地址（加进 应用 Redirect URLs）：${redirectUri()}`,
      `· 应用 Client ID（本页 Client ID）：${cfg.clientId || "<待填>"}`,
      "",
      "身份源 Identity > Providers → 添加：",
      `  企业微信  Type=WeCom，Sub type=Internal，Client ID=${w.corpId || "<CorpID>"}，Client secret=${w.secret || "<Secret>"}，Agent ID=${w.agentId || "<AgentId>"}`,
      `  钉钉      Type=DingTalk，Client ID=${d.appKey || "<AppKey>"}，Client secret=${d.appSecret || "<AppSecret>"}`,
      "",
      "把上面两个身份源加入 应用 的 Providers 列表后，登录页即出现对应入口。",
      "",
      "后端环境变量（改完重启后端）：",
      `  OIDC_ENABLED=1`,
      `  OIDC_ISSUER=${cfg.issuer || "<issuer>"}`,
      `  OIDC_AUDIENCE=${cfg.clientId || "<client_id>"}`,
      "",
      "注意：issuer 必须与 Casdoor 访问地址完全一致；回调地址必须与应用登记的一致。",
    ].join("\n");
  }, [cfg]);

  const copyGuide = async () => {
    try {
      await navigator.clipboard.writeText(guide);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  const doProvision = async () => {
    setProv(null);
    if (!cfg.issuer) {
      setProv({ error: "请先填 Issuer" });
      return;
    }
    setProvBusy(true);
    try {
      const r = await api.oidcProvision({
        issuer: cfg.issuer,
        admin_user: adminUser,
        admin_password: adminPass,
        app_name: appName,
        wecom: { corp_id: cfg.wecom.corpId, secret: cfg.wecom.secret, agent_id: cfg.wecom.agentId },
        dingtalk: { corp_id: cfg.dingtalk.appKey, secret: cfg.dingtalk.appSecret, agent_id: "" },
      });
      setProv(r);
    } catch (e: any) {
      setProv({ error: e?.message || String(e) });
    } finally {
      setProvBusy(false);
    }
  };

  return (
    <>
      <style>{STYLE}</style>
      <style>{AUTH_STYLE}</style>
      <div className="auth-wrap" style={{ alignItems: "flex-start" }}>
        <div className="auth-card wide">
          <div className="auth-brand">
            <div className="auth-logo">AI</div>
            <div style={{ fontSize: 12.5, color: "var(--muted)" }}>企业 AI 智能体中台 · 连接配置</div>
          </div>
          <h2>配置企业身份登录</h2>
          <p className="auth-sub">
            填入统一身份（OIDC）的 Issuer 与 Client ID 即可启用登录；企业微信 / 钉钉资料用于生成 Casdoor 配置。
          </p>

          <div className="section-title" style={{ marginTop: 4 }}>1. 统一身份（OIDC）</div>
          <div className="grid2">
            <div className="field">
              <label>Issuer（Casdoor 地址）</label>
              <input value={cfg.issuer} onChange={(e) => set({ issuer: e.target.value })} placeholder="http://127.0.0.1:8000" />
            </div>
            <div className="field">
              <label>Client ID（应用 App ID）</label>
              <input value={cfg.clientId} onChange={(e) => set({ clientId: e.target.value })} placeholder="在 Casdoor 应用里复制" />
            </div>
          </div>
          <div className="hint">回调地址：<span className="mono">{redirectUri()}</span>（需在 Casdoor 应用里登记）</div>

          <div className="section-title">2. 企业微信（可选）</div>
          <div className="grid2">
            <div className="field">
              <label>CorpID</label>
              <input value={cfg.wecom.corpId} onChange={(e) => setWecom({ corpId: e.target.value })} placeholder="企业微信 企业ID" />
            </div>
            <div className="field">
              <label>应用 Secret</label>
              <input value={cfg.wecom.secret} onChange={(e) => setWecom({ secret: e.target.value })} placeholder="自建应用 Secret" />
            </div>
            <div className="field">
              <label>AgentId</label>
              <input value={cfg.wecom.agentId} onChange={(e) => setWecom({ agentId: e.target.value })} placeholder="自建应用 AgentId" />
            </div>
          </div>

          <div className="section-title">3. 钉钉（可选）</div>
          <div className="grid2">
            <div className="field">
              <label>AppKey / Client ID</label>
              <input value={cfg.dingtalk.appKey} onChange={(e) => setDing({ appKey: e.target.value })} placeholder="钉钉 AppKey" />
            </div>
            <div className="field">
              <label>AppSecret / Client Secret</label>
              <input value={cfg.dingtalk.appSecret} onChange={(e) => setDing({ appSecret: e.target.value })} placeholder="钉钉 AppSecret" />
            </div>
          </div>

          <div className="auth-actions" style={{ marginTop: 16 }}>
            <button className="btn" onClick={doSave}>保存连接配置</button>
            <div className="row">
              <button className="btn ghost" style={{ flex: 1 }} onClick={copyGuide}>
                {copied ? "已复制 ✓" : "复制 Casdoor 填写清单"}
              </button>
              <button className="btn ghost" style={{ flex: 1 }} onClick={() => (window.location.href = "/")}>
                返回登录
              </button>
            </div>
          </div>
          {saved && <div className="ok">{saved}</div>}

          <div className="copybox" style={{ marginTop: 14 }}>{guide}</div>

          <div className="section-title">4. 进阶：一键写入 Casdoor（实验）</div>
          <div className="hint" style={{ marginBottom: 8 }}>
            填 Casdoor 管理员账号，我可尝试自动建 IdP 并关联到应用。失败时用上面的清单手动填（保证可用）。
          </div>
          <div className="grid2">
            <div className="field">
              <label>管理员账号</label>
              <input value={adminUser} onChange={(e) => setAdminUser(e.target.value)} />
            </div>
            <div className="field">
              <label>管理员密码</label>
              <input type="password" value={adminPass} onChange={(e) => setAdminPass(e.target.value)} placeholder="仅本次写入使用" />
            </div>
            <div className="field">
              <label>应用名（app name）</label>
              <input value={appName} onChange={(e) => setAppName(e.target.value)} placeholder="app-built-in" />
            </div>
          </div>
          <button className="btn ghost" disabled={provBusy} onClick={doProvision} style={{ marginTop: 6 }}>
            {provBusy ? "写入中…" : "一键写入 Casdoor"}
          </button>
          {prov && (
            <div className={prov.code === 0 ? "ok" : "err"} style={{ whiteSpace: "pre-wrap" }}>
              {prov.error
                ? `失败：${prov.error}`
                : `${prov.msg || (prov.code === 0 ? "已写入" : "失败")}\n${(prov.steps || []).join("\n")}`}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
