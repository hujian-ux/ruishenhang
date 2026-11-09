import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { api } from "../api/client";

// 模型配置：这是「让平台真的能答话」的那一页。
// 设计取舍：
// 1. 一个表单只问四个必答项（接入方式 / 厂商 / key / 模型名），其余给默认值；
// 2. 「保存」与「测试」分开——保存只落配置，测试才花钱调一次真模型；
// 3. 失败必须说人话：401 是"key 不是这家的"、402 是"欠费"、404 是"模型名不对"，
//    由后端 diagnose() 翻译，前端原样展示，不让用户去翻日志。

type Preset = {
  id: string; label: string; base_url: string; models: string[];
  small_model: string; key_env: string;
};

type Cfg = {
  provider: string; vendor: string; vendor_label: string; base_url: string;
  model: string; key_configured: boolean; key_hint: string; key_masked: string;
  temperature: number; max_tokens: number;
  presets: Preset[];
  routing: { enabled: boolean; small: string; large: string };
  registered: string[];
  env_file: string;
};

type Probe = {
  ok: boolean; model_id: string; status_code: number; reply: string;
  error: string; hint: string; latency_ms: number; base_url?: string;
};

const FIELD: CSSProperties = {
  width: "100%", padding: "8px 11px", border: "1px solid var(--line)", borderRadius: 8,
  fontSize: 13, outline: "none", fontFamily: "inherit", background: "#fff",
};
const LABEL: CSSProperties = { fontSize: 12, color: "var(--muted)", marginBottom: 4, display: "block" };

export function ModelsPage({ isAdmin }: { isAdmin: boolean }) {
  const [models, setModels] = useState<any[]>([]);
  const [gw, setGw] = useState<any>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);

  // 表单：从 cfg 初始化一次；之后以用户输入为准（不跟着刷新覆盖，否则打字被吃掉）
  const [provider, setProvider] = useState<"direct" | "litellm">("direct");
  const [vendor, setVendor] = useState("zhipu");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [routingOn, setRoutingOn] = useState(false);
  const [small, setSmall] = useState("");
  const [large, setLarge] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [probe, setProbe] = useState<Probe | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const [all, h, c] = await Promise.all([
        api.listModelsAll("all"), api.gatewayHealth(), api.getModelConfig(),
      ]);
      setModels(all.data.models);
      setGw(h.data);
      const conf: Cfg = c.data;
      setCfg(conf);
      setErr("");
      return conf;
    } catch (e: any) {
      setErr(e.message);
      return null;
    }
  };

  // 首次加载：把服务端配置灌进表单（之后不再覆盖，避免用户输入被刷新冲掉）
  useEffect(() => {
    load().then((conf) => {
      if (!conf) return;
      setProvider(conf.provider === "litellm" ? "litellm" : "direct");
      setVendor(conf.vendor && conf.vendor !== "litellm" ? conf.vendor : "zhipu");
      setBaseUrl(conf.base_url || "");
      setModel(conf.model || "");
      setRoutingOn(!!conf.routing?.enabled);
      setSmall(conf.routing?.small || "");
      setLarge(conf.routing?.large || "");
    });
  }, []);

  const preset = useMemo(
    () => cfg?.presets.find((p) => p.id === vendor),
    [cfg, vendor],
  );
  const suggestions = useMemo(() => {
    const s = new Set<string>(preset?.models || []);
    for (const r of cfg?.registered || []) s.add(r);
    return [...s].filter(Boolean);
  }, [preset, cfg]);

  const pickVendor = (id: string) => {
    setVendor(id);
    const p = cfg?.presets.find((x) => x.id === id);
    if (!p) return;
    setBaseUrl(p.base_url || "");
    if (!model && p.models[0]) setModel(p.models[0]);
    if (!small && p.small_model) setSmall(p.small_model);
  };

  const body = () => ({
    provider,
    vendor: provider === "direct" ? vendor : undefined,
    base_url: baseUrl.trim() || undefined,
    api_key: apiKey.trim() || undefined,   // 留空 = 不改动已有密钥
    model: model.trim(),
    routing: { enabled: routingOn, small: small.trim() || undefined, large: large.trim() || undefined },
  });

  const save = async (thenProbe: boolean) => {
    setErr(""); setMsg(""); setProbe(null);
    if (!isAdmin) { setErr("需要平台管理员身份（左上角身份可切换）"); return; }
    if (!model.trim()) { setErr("模型名不能为空：这一个字段决定真正调谁"); return; }
    setBusy(thenProbe ? "save+probe" : "save");
    try {
      const r = await api.saveModelConfig(body());
      setCfg(r.data.config);
      setGw(r.data.gateway);
      setApiKey("");   // 保存后不再留在输入框
      setMsg("已保存并落盘，重启后仍生效。");
      await load();
      if (thenProbe) await doProbe(model.trim());
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const doProbe = async (mid: string) => {
    setErr(""); setProbe(null);
    setBusy("probe");
    try {
      const r = await api.probeModel(mid || "");
      setProbe(r.data);
      // 探活也是状态灯的真实来源：通了就把灯点亮，不用等 30s 缓存过期
      setGw((g: any) => ({ ...g, available: r.data.ok }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const toggle = async (m: any) => {
    setErr(""); setMsg("");
    const next = m.status === "active" ? "inactive" : "active";
    try {
      await api.toggleModel(m.model_id, next);
      setMsg(`${m.model_id} 已${next === "active" ? "启用" : "停用"}（网关侧即时生效，业务代码零改动）`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <>
      <div className="card">
        <h3>模型网关状态（第25章 · 可插拔，业务侧只认 model_id）</h3>
        <div className="row" style={{ gap: 10, marginBottom: 8 }}>
          <div className="kpi">
            <span>通道状态</span>
            <b style={{ color: gw?.available ? "#2e7d32" : "#c62828" }}>
              {gw?.available ? "已就绪" : "未就绪（走 stub 回退）"}
            </b>
            <i>{gw?.provider === "direct" ? `直连 ${gw?.vendor_label || gw?.vendor}` : "LiteLLM 代理"}</i>
          </div>
          <div className="kpi">
            <span>默认模型</span><b style={{ fontSize: 15 }}>{gw?.default_model || "-"}</b>
            <i>{gw?.base_url}</i>
          </div>
          <div className="kpi">
            <span>密钥</span>
            <b style={{ fontSize: 14 }}>{gw?.key_configured ? "已配置" : "未配置"}</b>
            <i>{gw?.key_configured ? (cfg?.key_masked || "•") : gw?.key_hint}</i>
          </div>
          <div className="kpi"><span>注册模型</span><b>{models.length}</b>
            <i>{models.filter((m) => m.status === "active").length} 个启用中</i></div>
        </div>
        <div className="hint">
          {gw?.available
            ? "通道已就绪：对话、技能包、产出物都走这个模型。想换模型或厂商，在下面改，改完即时生效。"
            : "未就绪不影响平台可用：调用自动回退确定性 stub，照常计量落账（标记为 stub）。要真答话，配一个模型即可——就在下面。"}
        </div>
      </div>

      {/* ── 在线配置 ─────────────────────────────────────────────── */}
      <div className="card">
        <h3>配置模型（改完即时生效，重启不丢）</h3>
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828", marginBottom: 8 }}>{err}</div>}

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
          <div style={{ display: "inline-flex", border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
            {([["direct", "直连厂商"], ["litellm", "LiteLLM 代理"]] as const).map(([k, label]) => (
              <div key={k} onClick={() => isAdmin && setProvider(k)}
                   style={{ padding: "7px 14px", fontSize: 12.5, cursor: isAdmin ? "pointer" : "not-allowed",
                            background: provider === k ? "var(--ink)" : "#fff",
                            color: provider === k ? "#fff" : "var(--ink-2)" }}>
                {label}
              </div>
            ))}
          </div>
          <span className="hint" style={{ fontSize: 11.5, alignSelf: "center" }}>
            {provider === "direct"
              ? "填厂商 key 就能用，不需要额外起容器（推荐起步）"
              : "已有 LiteLLM 代理时选这个，多厂商统一账单"}
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 12 }}>
          {provider === "direct" && (
            <div>
              <label style={LABEL}>厂商</label>
              <select value={vendor} onChange={(e) => pickVendor(e.target.value)}
                      disabled={!isAdmin} style={FIELD}>
                {(cfg?.presets || []).map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              <div className="hint" style={{ fontSize: 11, marginTop: 4 }}>
                换厂商会自动填好接口地址和候选模型
              </div>
            </div>
          )}

          <div>
            <label style={LABEL}>
              {provider === "direct" ? "接口地址（base_url）" : "LiteLLM 地址"}
            </label>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                   placeholder={provider === "direct"
                     ? (preset?.base_url || "https://your-host/v1")
                     : "http://127.0.0.1:4000"}
                   disabled={!isAdmin} style={FIELD} />
            <div className="hint" style={{ fontSize: 11, marginTop: 4 }}>
              留空则用该厂商默认地址；自建/私有化必须填
            </div>
          </div>

          <div>
            <label style={LABEL}>API Key</label>
            <input value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                   type="password" autoComplete="new-password"
                   placeholder={cfg?.key_configured ? `已配置 ${cfg.key_masked}（留空不改）` : "粘贴厂商密钥"}
                   disabled={!isAdmin} style={FIELD} />
            <div className="hint" style={{ fontSize: 11, marginTop: 4 }}>
              只存在本机 {cfg?.env_file ? "backend/.env.local" : ".env.local"}；生产请用密钥管理服务
            </div>
          </div>

          <div>
            <label style={LABEL}>默认模型</label>
            <input value={model} onChange={(e) => setModel(e.target.value)}
                   list="model-suggest" placeholder={suggestions[0] || "glm-4.6"}
                   disabled={!isAdmin} style={FIELD} />
            <datalist id="model-suggest">
              {suggestions.map((s) => <option key={s} value={s} />)}
            </datalist>
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 5 }}>
              {(preset?.models || []).slice(0, 4).map((m) => (
                <span key={m} onClick={() => isAdmin && setModel(m)}
                      style={{ fontSize: 10.5, color: model === m ? "#fff" : "var(--ink-2)",
                               background: model === m ? "var(--ink)" : "#f6f6f2",
                               borderRadius: 5, padding: "2px 7px",
                               cursor: isAdmin ? "pointer" : "default" }}>{m}</span>
              ))}
            </div>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <span onClick={() => setShowAdvanced((v) => !v)}
                style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
            {showAdvanced ? "▾" : "▸"} 高级：成本路由（按任务复杂度换小模型省钱）
          </span>
          {showAdvanced && (
            <div style={{ marginTop: 8, padding: "10px 12px", background: "#fcfcfa",
                          border: "1px solid var(--line)", borderRadius: 8 }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5 }}>
                <input type="checkbox" checked={routingOn} disabled={!isAdmin}
                       onChange={(e) => setRoutingOn(e.target.checked)} />
                开启成本路由
                <span className="hint" style={{ fontSize: 11 }}>
                  关闭后所有请求都走上面的默认模型（单模型接入建议关闭）
                </span>
              </label>
              {routingOn && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
                  <div>
                    <label style={LABEL}>简单任务用小模型</label>
                    <input value={small} onChange={(e) => setSmall(e.target.value)}
                           placeholder={model || "留空=同默认模型"}
                           disabled={!isAdmin} style={FIELD} />
                  </div>
                  <div>
                    <label style={LABEL}>复杂任务用大模型</label>
                    <input value={large} onChange={(e) => setLarge(e.target.value)}
                           placeholder={model || "留空=同默认模型"}
                           disabled={!isAdmin} style={FIELD} />
                  </div>
                </div>
              )}
              <div className="hint" style={{ fontSize: 11, marginTop: 8 }}>
                路由按关键词+长度分档，不是模型能力评测；两档留空会自动对齐到默认模型，
                避免换厂商后路由还把请求改写回旧模型名（那会直接 401）。
              </div>
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
          <button className="btn" disabled={!isAdmin || !!busy}
                  style={{ padding: "7px 16px", opacity: isAdmin ? 1 : .45 }}
                  onClick={() => save(true)}>
            {busy === "save+probe" ? "保存并测试中…" : "保存并测试"}
          </button>
          <button className="btn" disabled={!isAdmin || !!busy}
                  style={{ padding: "7px 14px", background: "#fff", color: "var(--ink-2)",
                           border: "1px solid var(--line)", opacity: isAdmin ? 1 : .45 }}
                  onClick={() => save(false)}>
            {busy === "save" ? "保存中…" : "仅保存"}
          </button>
          <button className="btn" disabled={!!busy || !model}
                  style={{ padding: "7px 14px", background: "#fff", color: "var(--ink-2)",
                           border: "1px solid var(--line)" }}
                  onClick={() => doProbe(model)}>
            {busy === "probe" ? "测试中…" : "只测当前配置"}
          </button>
          {!isAdmin && (
            <span className="hint" style={{ fontSize: 11.5, alignSelf: "center" }}>
              配置模型需要平台管理员身份（左上角身份可切换）
            </span>
          )}
        </div>

        {/* 探活结论：成功/失败都给下一步，不让人猜 */}
        {probe && (
          <div style={{ marginTop: 12, border: `1px solid ${probe.ok ? "#bfe3cd" : "#f0c2c2"}`,
                        background: probe.ok ? "#f4fbf7" : "#fdf5f5", borderRadius: 10, padding: "12px 14px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <b style={{ fontSize: 13, color: probe.ok ? "#2e7d32" : "#c62828" }}>
                {probe.ok ? "模型已连通" : "模型不可用"}
              </b>
              <span className="tag">{probe.model_id}</span>
              <span className="hint" style={{ fontSize: 11 }}>{probe.latency_ms}ms · HTTP {probe.status_code || "-"}</span>
            </div>
            {probe.ok ? (
              <div style={{ fontSize: 13 }}>
                模型回了：<b>「{probe.reply || "（空）"}」</b>
                <span className="hint" style={{ fontSize: 11.5, marginLeft: 8 }}>
                  现在去对话页发一句话，就真的由它回答了
                </span>
              </div>
            ) : (
              <div style={{ fontSize: 12.5, lineHeight: 1.8 }}>
                <div>{probe.hint || "调用失败，看下面的原文"}</div>
                <div className="hint" style={{ fontSize: 11.5, marginTop: 4, wordBreak: "break-all" }}>
                  {probe.error}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 注册表 ─────────────────────────────────────────────── */}
      <div className="card">
        <h3>模型注册表</h3>
        <table className="tbl">
          <thead>
            <tr><th>model_id</th><th>厂商</th><th>适配器</th><th>能力</th><th>上下文</th><th>单价（元/1k）</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            {models.map((m: any) => (
              <tr key={m.model_id}>
                <td><b>{m.model_id}</b>{m.model_id === gw?.default_model && <span className="tag" style={{ marginLeft: 6 }}>默认</span>}</td>
                <td>{m.vendor}</td>
                <td className="hint">{m.adapter}</td>
                <td>{(m.capabilities || []).join(" / ")}</td>
                <td>{m.context_window ? `${(m.context_window / 1000).toFixed(0)}k` : "-"}</td>
                <td>
                  {m.price_per_1k_prompt} / {m.price_per_1k_completion}
                  {m.unpriced && <span className="tag" style={{ marginLeft: 6, background: "#b8791f", color: "#fff" }}>未定价</span>}
                </td>
                <td>{m.status === "active"
                  ? <span className="tag" style={{ background: "#2e7d32", color: "#fff" }}>active</span>
                  : <span className="tag" style={{ background: "#8d99ae", color: "#fff" }}>inactive</span>}</td>
                <td>
                  <button
                    className="btn"
                    style={{ padding: "4px 9px", fontSize: 11.5, background: m.status === "active" ? "#c62828" : "#2e7d32", opacity: isAdmin ? 1 : 0.45 }}
                    disabled={!isAdmin}
                    onClick={() => toggle(m)}
                  >
                    {m.status === "active" ? "停用" : "启用"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hint" style={{ marginTop: 8 }}>
          单价为牌价参考，真实合同价用 `MODEL_PRICES_JSON` 覆盖；标「未定价」的模型按 0 元入账，
          账单上能看出"这个还没定价"，不会静悄悄算错。
        </div>
      </div>
    </>
  );
}
