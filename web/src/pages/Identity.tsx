import { useEffect, useState } from "react";
import { api } from "../api/client";
import { loadConfig, isConfigured } from "../auth/config";

/** P1-173 · 身份与集成：把 OIDC 收口状态 + 企微/钉钉 运行时消息接入集中在一屏。
 *  只读真实返回；没配就写「未就绪」，不把空画成已接入。 */
type Any = Record<string, any>;

const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--card)" };
const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const mono: React.CSSProperties = { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12 };

function KV({ label, value, hint }: { label: string; value: any; hint?: string }) {
  return (
    <div style={{ ...box, padding: 12 }}>
      <div style={small}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: "var(--navy)", marginTop: 4 }}>{String(value)}</div>
      {hint && <div style={{ ...small, marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

export function IdentityPage({ isAdmin }: { isAdmin: boolean }) {
  const [err, setErr] = useState("");
  const [auth, setAuth] = useState<Any>({});
  const [configs, setConfigs] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  // IM 表单
  const cfg = loadConfig();
  const [provider, setProvider] = useState<"wecom" | "dingtalk">("wecom");
  const [corpId, setCorpId] = useState("");
  const [secret, setSecret] = useState("");
  const [agentId, setAgentId] = useState("");
  const [webhookToken, setWebhookToken] = useState("");
  const [aesKey, setAesKey] = useState("");

  // 测试下发
  const [testUser, setTestUser] = useState("");
  const [testText, setTestText] = useState("这是一条来自睿深行 AI 工作台的测试消息");
  const [testOut, setTestOut] = useState<any>(null);

  const load = async () => {
    try {
      const [a, c] = await Promise.all([api.getAuthSettings(), api.listImConfigs()]);
      setAuth(a.data || {});
      setConfigs(c.data || []);
      setErr("");
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { if (isAdmin) load(); }, [isAdmin]);

  const save = async () => {
    setBusy(true); setErr(""); setTestOut(null);
    try {
      await api.saveImConfig({
        provider, corp_id: corpId, secret, agent_id: agentId,
        webhook_token: webhookToken, encoding_aes_key: aesKey,
      });
      setSecret(""); setWebhookToken(""); setAesKey(""); // 密钥不回显
      await load();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const del = async (p: string) => {
    setErr("");
    try { await api.deleteImConfig(p); await load(); } catch (e: any) { setErr(e.message); }
  };

  const send = async () => {
    setErr(""); setTestOut(null);
    try {
      const r = await api.sendImMessage({ provider, to_user: testUser, text: testText });
      setTestOut(r.data || r);
    } catch (e: any) { setErr(e.message); }
  };

  const approve = async () => {
    setErr(""); setTestOut(null);
    try {
      const r = await api.imApprove({
        provider, to_user: testUser,
        title: "【待审批】测试审批单", url: window.location.origin,
      });
      setTestOut(r.data || r);
    } catch (e: any) { setErr(e.message); }
  };

  if (!isAdmin) {
    return <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a" }}>仅平台管理员可访问「身份与集成」。</div>;
  }

  const localReady = isConfigured(cfg);
  const enforce = !!auth.enforce_oidc;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>身份与集成 · P1-173</h2>
          <div style={small}>统一身份（OIDC 收口）状态 + 企业微信/钉钉 运行时消息接入。所有状态来自真实返回值。</div>
        </div>
        <button className="btn" onClick={load}>刷新</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>操作失败：{err}</div>}

      <div className="grid4">
        <KV label="OIDC 身份源" value={auth.oidc_enabled ? "已开启" : "未开启"} hint="后端 OIDC_ENABLED" />
        <KV label="身份收口" value={enforce ? "已开启" : "未开启"} hint="AUTH_ENFORCE_OIDC：开则禁止 dev 捷径" />
        <KV label="前端身份源" value={localReady ? "已填写" : "未填"} hint="本机 Setup 页填的 Client ID" />
        <KV label="IM 接入" value={`${configs.length} 个`} hint="企微/钉钉 运行时配置" />
      </div>

      <div className="grid2" style={{ marginTop: 14 }}>
        <div className="card">
          <h3>① 统一身份（OIDC）</h3>
          <div style={{ ...small, marginBottom: 8 }}>收口状态与后端配置；前端填凭证在「配置身份源」页完成。</div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <tbody>
              <tr style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 4px", color: "var(--muted)" }}>Issuer（脱敏）</td>
                <td style={{ ...mono, padding: "6px 4px" }}>{auth.issuer_masked || "—"}</td>
              </tr>
              <tr style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 4px", color: "var(--muted)" }}>Client ID（脱敏）</td>
                <td style={{ ...mono, padding: "6px 4px" }}>{auth.client_id_masked || "—"}</td>
              </tr>
              <tr style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 4px", color: "var(--muted)" }}>收口生效</td>
                <td style={{ padding: "6px 4px", color: enforce ? "#1b7f4b" : "#b06a1b" }}>
                  {enforce ? "是：登录页演示入口已隐藏，强制走真实 SSO" : "否：演示入口可用（开发/演示环境）"}
                </td>
              </tr>
            </tbody>
          </table>
          <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
            <button className="btn" onClick={() => (window.location.href = "/setup")}>配置身份源</button>
            <button className="btn ghost" onClick={() => window.open("/api/v1/auth/oidc/health", "_blank")}>查 OIDC 健康</button>
          </div>
          {!localReady && <div className="warn" style={{ marginTop: 10 }}>前端尚未填写 Client ID：去「配置身份源」填 Issuer + Client ID，登录页才会出现企业身份入口。</div>}
        </div>

        <div className="card">
          <h3>② 企业微信 / 钉钉 · 消息接入（运行时）</h3>
          <div style={{ ...small, marginBottom: 8 }}>这是真正收发消息/回推审批的配置（与「配置身份源」里给 Casdoor 的资料是两回事）。密钥仅存后端内存、列表脱敏。</div>
          <div className="grid2">
            <div className="field" style={{ gridColumn: "1 / -1" }}>
              <label>平台</label>
              <select value={provider} onChange={(e) => setProvider(e.target.value as any)} style={{ width: "100%" }}>
                <option value="wecom">企业微信</option>
                <option value="dingtalk">钉钉</option>
              </select>
            </div>
            <div className="field">
              <label>{provider === "wecom" ? "CorpID" : "AppKey / Client ID"}</label>
              <input value={corpId} onChange={(e) => setCorpId(e.target.value)} placeholder={provider === "wecom" ? "企业微信企业ID" : "钉钉 AppKey"} />
            </div>
            <div className="field">
              <label>{provider === "wecom" ? "CorpSecret" : "AppSecret"}</label>
              <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="应用 Secret" />
            </div>
            <div className="field">
              <label>AgentId（企微必填）</label>
              <input value={agentId} onChange={(e) => setAgentId(e.target.value)} placeholder="自建应用 AgentId" />
            </div>
            <div className="field">
              <label>{provider === "wecom" ? "回调 Token" : "加签密钥"}</label>
              <input type="password" value={webhookToken} onChange={(e) => setWebhookToken(e.target.value)} placeholder="回调验签用" />
            </div>
            {provider === "wecom" && (
              <div className="field" style={{ gridColumn: "1 / -1" }}>
                <label>EncodingAESKey（可选，消息体解密）</label>
                <input type="password" value={aesKey} onChange={(e) => setAesKey(e.target.value)} placeholder="43 位，未配则消息体不解密" />
              </div>
            )}
          </div>
          <button className="btn" disabled={busy} onClick={save} style={{ marginTop: 10 }}>
            {busy ? "保存中…" : "保存接入配置"}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>③ 已接入列表与测试</h3>
        <div style={{ ...small, marginBottom: 8 }}>密钥脱敏展示；测试下发/审批回推会真实调用对应平台（需先填真实凭证）。</div>
        {configs.length === 0 && <div style={small}>暂无接入配置。在上方保存后会出现在这里。</div>}
        {configs.map((c: Any) => (
          <div key={c.provider} style={{ display: "flex", alignItems: "center", gap: 8, borderTop: "1px solid var(--line)", padding: "8px 0" }}>
            <span className="badge">{c.provider}</span>
            <span style={{ ...mono, fontSize: 12 }}>corp_id={c.corp_id} · agent_id={c.agent_id || "—"} · secret={c.secret}</span>
            <span style={{ ...small, marginLeft: "auto" }}>更新 {c.updated_at || "—"}</span>
            <button className="btn ghost" onClick={() => del(c.provider)}>删除</button>
          </div>
        ))}

        <div className="grid2" style={{ marginTop: 12 }}>
          <div className="field">
            <label>测试接收人（工号/UserId）</label>
            <input value={testUser} onChange={(e) => setTestUser(e.target.value)} placeholder="如 1001 / 张三的 userid" />
          </div>
          <div className="field">
            <label>测试文本</label>
            <input value={testText} onChange={(e) => setTestText(e.target.value)} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button className="btn" disabled={!testUser} onClick={send}>下发测试消息</button>
          <button className="btn ghost" disabled={!testUser} onClick={approve}>测试审批回推</button>
        </div>
        {testOut && (
          <pre style={{ ...mono, whiteSpace: "pre-wrap", background: "var(--soft)", padding: 8, borderRadius: 6, marginTop: 8, maxHeight: 160, overflow: "auto" }}>
            {JSON.stringify(testOut, null, 2)}
          </pre>
        )}
        <div style={{ ...small, marginTop: 8 }}>
          注：真收发需后端能访问企微/钉钉网关；企微消息体 AES 解密待 EncodingAESKey + pycryptodome（已留占位）。
        </div>
      </div>
    </div>
  );
}
