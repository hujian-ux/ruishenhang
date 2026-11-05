import { useMemo, useState } from "react";
import { api } from "../api/client";
import { AUTH_STYLE, STYLE } from "../theme";
import { isConfigured, loadConfig, redirectUri } from "../auth/config";
import { buildAuthorizeUrl, pkceChallenge, randomString } from "../auth/oidc";
import { setSession } from "../auth/session";

// 登录页：对外产品第一屏。左侧讲清楚"这是什么、给谁用"，右侧只放登录动作。
export function LoginPage() {
  const cfg = useMemo(() => loadConfig(), []);
  const configured = isConfigured(cfg);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [devBusy, setDevBusy] = useState(false);

  const oidcLogin = async () => {
    setErr("");
    if (!configured) {
      setErr("尚未填写 Client ID，请先点击「配置身份源」。");
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
      setErr(`演示登录失败：${e?.message || e}`);
      setDevBusy(false);
    }
  };

  return (
    <>
      <style>{STYLE}</style>
      <style>{AUTH_STYLE}</style>
      <div className="auth-screen">
        <div className="auth-left">
          <div className="brand">
            <div className="lg">睿</div>
            <div>
              <b>睿深行 AI 工作台</b>
              <i>企业智能体中台</i>
            </div>
          </div>

          <div className="auth-hero">
            <h1>把 115 个业务技能<br />交到每位员工手里</h1>
            <p>
              上传台账、报表、合同，技能包按自己的流程逐步执行，
              产出 Excel / Word / PPT / PDF 直接下载归档。
            </p>
            <div className="auth-points">
              <div><b>✓</b>企业微信 / 钉钉身份直接登录，权限按部门隔离</div>
              <div><b>✓</b>技能包先评审后发布，未发布一律不可调用</div>
              <div><b>✓</b>对话与文件仅本人可见，全程留痕可审计</div>
            </div>
          </div>

          <div className="foot">© 睿深行 · 数据不出企业内网</div>
        </div>

        <div className="auth-right">
          <div className="auth-card">
            <h2>登录</h2>
            <p className="auth-sub">使用企业统一身份继续</p>

            {!configured && (
              <div className="warn" style={{ marginBottom: 0 }}>
                尚未接入企业身份源。可先点「配置身份源」填写，或用下方演示入口进入。
              </div>
            )}
            {configured && (
              <div className="hint">
                身份源 <span className="mono">{cfg.issuer.replace(/^https?:\/\//, "").slice(0, 34)}</span>
              </div>
            )}

            <div className="auth-actions">
              <button className="btn blue" disabled={busy || !configured} onClick={oidcLogin}>
                {busy ? "正在跳转…" : "使用企业身份登录"}
              </button>
              <button className="btn ghost" onClick={() => (window.location.href = "/setup")}>
                配置身份源
              </button>
            </div>

            {err && <div className="err">{err}</div>}

            <div className="auth-rule">演示</div>
            <div className="auth-foot">
              <span className="link" onClick={() => !devBusy && devLogin(false)}>员工视角</span>
              <span style={{ color: "var(--line)" }}>|</span>
              <span className="link" onClick={() => !devBusy && devLogin(true)}>管理员视角</span>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
