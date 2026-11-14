import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { AUTH_STYLE, STYLE } from "../theme";
import { setSession } from "../auth/session";

// 飞书回调：读 code/state → 调后端换身份 → 存 sso 令牌 → 回首页。
// 飞书不发 OIDC id_token，故由后端换码 + 取 user_info 验明身份后自签 `sso` 会话令牌。
export function FeishuCallbackPage() {
  const [msg, setMsg] = useState("正在使用飞书完成登录…");
  const [err, setErr] = useState("");
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode 双执行保护
    started.current = true;

    const run = async () => {
      const q = new URLSearchParams(window.location.search);
      const code = q.get("code") || "";
      const state = q.get("state") || "";
      const oerr = q.get("error");
      if (oerr) {
        setErr(`飞书返回错误：${oerr} ${q.get("error_description") || ""}`);
        setMsg("");
        return;
      }
      if (!code) {
        setErr("回调缺少 code 参数。请重新发起登录。");
        setMsg("");
        return;
      }
      const expectState = sessionStorage.getItem("rsp_feishu_state") || "";
      if (expectState && state !== expectState) {
        setErr("state 校验失败（可能被劫持或标签页过期），请重新登录。");
        setMsg("");
        return;
      }
      try {
        const r = await api.feishuCallback({
          code,
          redirect_uri: `${window.location.origin}/callback/feishu`,
        });
        const token = r.data.token;
        const u = r.data.user || {};
        setSession({
          idToken: token,
          user: {
            userId: u.userId ?? "feishu_user",
            name: u.name ?? u.userId ?? "飞书用户",
            deptId: u.deptId ?? "",
            role: u.role ?? "employee",
          },
          dev: false,
        });
        sessionStorage.removeItem("rsp_feishu_state");
        setMsg("登录成功，正在进入…");
        window.location.href = "/";
      } catch (e: any) {
        setErr(`飞书换码失败：${e?.message || e}`);
        setMsg("");
      }
    };
    run();
  }, []);

  return (
    <>
      <style>{STYLE}</style>
      <style>{AUTH_STYLE}</style>
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="auth-brand">
            <div className="auth-logo">AI</div>
            <div style={{ fontSize: 12.5, color: "var(--muted)" }}>企业 AI 智能体中台</div>
          </div>
          <h2>正在登录</h2>
          {msg && <p className="auth-sub">{msg}</p>}
          {err && (
            <>
              <div className="err">{err}</div>
              <div className="auth-actions" style={{ marginTop: 14 }}>
                <button className="btn" onClick={() => (window.location.href = "/")}>
                  返回登录
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
