import { useMemo, useState } from "react";
import { api } from "../api/client";
import { AUTH_STYLE, STYLE } from "../theme";
import { isConfigured, loadConfig, redirectUri } from "../auth/config";
import { buildAuthorizeUrl, pkceChallenge, randomString } from "../auth/oidc";
import { setSession } from "../auth/session";

// 登录页：企业身份（企业微信 / 钉钉 / SSO）走标准 OIDC；另留开发模式入口。
export function LoginPage() {
  const cfg = useMemo(() => loadConfig(), []);
  const configured = isConfigured(cfg);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [devBusy, setDevBusy] = useState(false);

  const oidcLogin = async () => {
    setErr("");
    if (!configured) {
      setErr("尚未填写 Client ID，请先点击「配置连接」。");
      return;
    }
    setBusy(true);
    try {
      const verifier = randomString(64);
      const challenge = await pkceChallenge(verifier);
      const state = randomString(24);
      sessionStorage.setItem("rsp_oidc_verifier", verifier);
      sessionStorage.setItem("rsp_oidc_state", state);
      window.location.href = buildAuthorizeUrl(cfg.issuer, cfg.clientId, redirectUri(), state, challenge);
    } catch (e: any) {
      setErr(`发起登录失败：${e?.message || e}`);
      setBusy(false);
    }
  };

  const devLogin = async (admin: boolean) => {
    setErr("");
    setDevBusy(true);
    try {
      const r = await api.login(admin ? "dev_admin" : "dev");
      setSession({
        idToken: r.data.token,
        user: {
          userId: r.data.user?.userId ?? "u_dev",
          name: r.data.user?.name ?? "开发用户",
          deptId: r.data.user?.deptId ?? "d_demo",
          role: r.data.user?.role ?? "employee",
        },
        dev: true,
      });
      window.location.href = "/";
    } catch (e: any) {
      setErr(`开发模式登录失败：${e?.message || e}`);
      setDevBusy(false);
    }
  };

  return (
    <>
      <style>{STYLE}</style>
      <style>{AUTH_STYLE}</style>
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="auth-brand">
            <div className="auth-logo">AI</div>
            <div>
              <div style={{ fontSize: 12.5, color: "var(--muted)", letterSpacing: 0.5 }}>睿深行 · 企业 AI 智能体中台</div>
            </div>
          </div>
          <h2>登录</h2>
          <p className="auth-sub">使用企业身份登录（企业微信 / 钉钉 / 统一 SSO）。</p>

          {configured ? (
            <div className="hint" style={{ marginBottom: 12 }}>
              身份源：<span className="mono">{cfg.issuer}</span>
              <br />Client ID：<span className="mono">{cfg.clientId}</span>
            </div>
          ) : (
            <div className="warn">
              尚未配置企业身份源（缺少 Client ID）。可先用下方「开发模式」进入，或点击「配置连接」填写。
            </div>
          )}

          <div className="auth-actions">
            <button className="btn" disabled={busy || !configured} onClick={oidcLogin}>
              {busy ? "正在跳转…" : "使用企业身份登录"}
            </button>
            <button className="btn ghost" onClick={() => (window.location.href = "/setup")}>
              配置连接（企微 / 钉钉 / SSO）
            </button>
          </div>

          {err && <div className="err">{err}</div>}

          <div className="auth-foot">
            <span>开发者 / 演示：</span>
            <span>
              <span className="link" onClick={() => !devBusy && devLogin(false)}>员工</span>
              <span style={{ color: "var(--line)", margin: "0 6px" }}>|</span>
              <span className="link" onClick={() => !devBusy && devLogin(true)}>管理员</span>
            </span>
          </div>
        </div>
      </div>
    </>
  );
}
