import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** P1-12 · 上下文窗口与压缩（#307）。
 *  窗口口径统一到后端配置；压缩默认关，开启后被窗口截掉的早期轮次生成摘要。
 *  本页只做只读预览：不写会话、不改配置；模型通道不可用时如实标注回落，
 *  绝不把 stub 文本或空壳摘要冒充成「已压缩」。 */
type Any = Record<string, any>;
type Mode = "session" | "custom";
type CompressMode = "config" | "on" | "off";

const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--card)" };
const pre: React.CSSProperties = {
  margin: 0, padding: 10, background: "var(--bg)", borderRadius: 6, fontSize: 12,
  whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 260, overflowY: "auto",
};

// 内置一段有内容的多轮咨询，便于在没有真实长会话时直接看清压缩前后差别。
function sampleTurns(n: number): any[] {
  const topics = ["五一活动方案", "预算上限", "主视觉定稿", "投放渠道排期", "门店物料清单",
    "社群裂变机制", "异业合作名单", "复盘指标口径"];
  const facts = ["列出三套候选方案并附投入产出测算", "预算封顶 18 万，超支需二次审批",
    "主视觉沿用春季版，仅替换主标题", "抖音与企微双通道，先小范围试投",
    "物料按 32 家门店分批配送", "裂变以老客邀约为主，不设现金奖励",
    "已确认 3 家异业伙伴，合同待法务复核", "复盘只看核销率与复购率两项"];
  const out: any[] = [];
  for (let i = 0; i < n; i += 1) {
    const k = Math.floor(i / 2) % topics.length;
    out.push(i % 2 === 0
      ? { role: "user", content: `第${k + 1}轮：${topics[k]}怎么定？请结合前面已经确认的结论一起看。` }
      : { role: "assistant", content: `第${k + 1}轮结论：${facts[k]}。补充说明与依据见上一版方案，此处不重复展开。` });
  }
  return out;
}

export function ContextWindowPage() {
  const [status, setStatus] = useState<Any>({});
  const [sessions, setSessions] = useState<any[]>([]);
  const [mode, setMode] = useState<Mode>("custom");
  const [sessionId, setSessionId] = useState("");
  const [cw, setCw] = useState("");
  const [runtime, setRuntime] = useState(true);
  const [compressMode, setCompressMode] = useState<CompressMode>("on");
  const [sampleCount, setSampleCount] = useState(20);
  const [detail, setDetail] = useState<Any | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([api.getContextStatus(), api.listSessions(50)]);
      setStatus(s.data || {});
      setSessions(list.data?.sessions || []);
    } catch (ex: any) { setErr(ex.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const compressArg = compressMode === "config" ? undefined : compressMode === "on";
  const cwArg = cw.trim() === "" ? undefined : Number(cw);

  const run = async () => {
    setBusy(true); setErr(""); setDetail(null);
    try {
      let r: any;
      if (mode === "session") {
        if (!sessionId) throw new Error("先选一个会话");
        r = await api.previewContext({
          session_id: sessionId, context_window: cwArg, compress: compressArg, runtime,
        });
      } else {
        r = await api.previewContextCustom({
          turns: sampleTurns(sampleCount), context_window: cwArg,
          compress: compressMode === "config" ? null : compressArg, runtime,
        });
      }
      setDetail(r.data || {});
    } catch (ex: any) { setErr(ex.message); }
    setBusy(false);
  };

  const sum: Any = detail?.summary || {};
  const on = !!status.compress_enabled;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>上下文窗口 · P1-12</h2>
          <div style={small}>
            会话窗口统一由 CTX_* 配置；超窗的早期轮次可压成摘要注入。默认压缩关闭，输出与改动前逐字节一致。
          </div>
        </div>
        <button className="btn" onClick={load}>刷新配置</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>{err}</div>}

      <div className="grid4">
        <div className="kpi"><span>会话存储上限</span><b>{status.session_turns ?? 0}<span style={{ fontSize: 13 }}> 轮</span></b>
          <i>CTX_SESSION_TURNS · 滑动丢弃</i></div>
        <div className="kpi"><span>会话详情窗口</span><b>{status.window_turns ?? 0}<span style={{ fontSize: 13 }}> 轮</span></b>
          <i>CTX_WINDOW_TURNS · context_preview</i></div>
        <div className="kpi"><span>生成器窗口</span><b>{status.runtime_turns ?? 0}<span style={{ fontSize: 13 }}> 轮</span></b>
          <i>CTX_RUNTIME_TURNS · 5 条路径同口径</i></div>
        <div className="kpi"><span>历史压缩</span>
          <b style={{ color: on ? "var(--ok)" : "var(--warn)" }}>{on ? "已启用" : "未启用"}</b>
          <i>{on ? (status.compress_provider || "extractive") : "CTX_COMPRESS_ENABLED=0"}</i></div>
      </div>
      <div style={{ ...small, marginTop: 8 }}>
        请求级覆盖范围 [{status.min_turns ?? 1}, {status.max_turns ?? 40}] · 摘要上限 {status.compress_max_chars ?? 0} 字 ·
        单轮摘录 {status.compress_turn_chars ?? 0} 字 · 已注册通道 {(status.providers || []).join(" / ") || "—"} ·
        兼容别名 MAX_SESSION_TURNS={status.legacy_aliases?.MAX_SESSION_TURNS ?? "—"} / MAX_CONTEXT_TURNS={status.legacy_aliases?.MAX_CONTEXT_TURNS ?? "—"}
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3 style={{ marginTop: 0 }}>① 预览会真正注入模型的上下文</h3>
        <div className="row" style={{ flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          <label style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="radio" checked={mode === "custom"} onChange={() => setMode("custom")} /> 示例轮次试算
          </label>
          <label style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="radio" checked={mode === "session"} onChange={() => setMode("session")} /> 真实会话
          </label>
          {mode === "custom" ? (
            <span style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
              轮数
              <input type="number" min={1} max={200} value={sampleCount}
                onChange={(e) => setSampleCount(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
                style={{ width: 80 }} />
            </span>
          ) : (
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)} style={{ minWidth: 260 }}>
              <option value="">选择会话（{sessions.length}）</option>
              {sessions.map((s: any) => (
                <option key={s.session_id} value={s.session_id}>
                  {s.title || s.session_id} · {s.turn_count} 轮
                </option>
              ))}
            </select>
          )}
          <span style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
            context_window
            <input value={cw} onChange={(e) => setCw(e.target.value)} placeholder="不传=用配置"
              style={{ width: 130 }} />
          </span>
          <span style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
            压缩
            <select value={compressMode} onChange={(e) => setCompressMode(e.target.value as CompressMode)}>
              <option value="config">按全局配置</option>
              <option value="on">强制开启</option>
              <option value="off">强制关闭</option>
            </select>
          </span>
          <label style={{ ...small, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={runtime} onChange={(e) => setRuntime(e.target.checked)} /> 生成器口径
          </label>
          <button className="btn" onClick={run} disabled={busy}>{busy ? "计算中…" : "预览"}</button>
        </div>
        <div style={{ ...small, marginTop: 8 }}>
          「生成器口径」勾选时用 runtime_turns（默认 2 轮），不勾用会话详情口径（默认 6 轮）；
          请求级 context_window 会自动夹取到合法区间，传 0 会被夹到 1。
        </div>
      </div>

      {detail && (
        <div className="card" style={{ marginTop: 14 }}>
          <h3 style={{ marginTop: 0 }}>② 预览结果</h3>
          <div className="grid4">
            <div className="kpi"><span>总轮数</span><b>{detail.total_turns ?? 0}</b><i>会话内全部轮次</i></div>
            <div className="kpi"><span>生效窗口</span><b>{detail.window_turns ?? 0}</b>
              <i>{detail.runtime ? "生成器口径" : "会话详情口径"}</i></div>
            <div className="kpi"><span>近窗保留</span><b>{detail.kept_turns ?? 0}</b><i>原文逐条注入</i></div>
            <div className="kpi"><span>被压缩</span><b>{detail.dropped_turns ?? 0}</b>
              <i>{sum.enabled ? "进历史摘要" : "直接丢弃（压缩未开）"}</i></div>
          </div>
          <div style={{ ...small, marginTop: 8 }}>
            注入文本 {detail.context_chars ?? 0} 字 · 粗估 {detail.estimated_tokens ?? 0} tokens（仅作窗口宽窄参考，不是计量口径）
          </div>

          <div style={{ ...box, marginTop: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
              <b>历史摘要</b>
              <span style={small}>
                {!sum.enabled ? "未启用压缩" :
                  `${sum.real ? "模型摘要" : "抽取摘要"} · ${sum.provider || "—"} · ${sum.lines ?? 0} 条` +
                  (sum.truncated ? " · 已截断" : "") +
                  (sum.fallback ? " · 通道回落" : "")}
              </span>
            </div>
            {sum.fallback && (
              <div style={{ ...small, marginTop: 6, color: "var(--warn)" }}>
                请求的 {sum.requested_provider || "llm"} 通道不可用，已回落 extractive：{sum.error || "未给出原因"}。
                这里展示的是确定性摘录，不是模型生成结论。
              </div>
            )}
            {!sum.enabled && (
              <div style={{ ...small, marginTop: 6 }}>
                压缩未开启时，被窗口截掉的早期轮次直接丢弃，输出与改动前逐字节一致。
              </div>
            )}
            <pre style={{ ...pre, marginTop: 8 }}>{sum.text || "（无摘要）"}</pre>
            <div style={{ ...small, marginTop: 6 }}>
              摘要注入时会明确标注「仅作线索，可能不完整，以下近窗原文为准」，避免模型把二手摘要当一手事实。
            </div>
          </div>

          <div style={{ ...box, marginTop: 10 }}>
            <b>近窗原文</b>
            <pre style={{ ...pre, marginTop: 8 }}>{detail.recent_text || "（无）"}</pre>
          </div>

          <div style={{ ...box, marginTop: 10 }}>
            <b>完整注入文本</b>
            <div style={small}>即 build_context 的返回值，生成路径会把它拼进「对话记忆」。</div>
            <pre style={{ ...pre, marginTop: 8 }}>{detail.context || "（空）"}</pre>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h3 style={{ marginTop: 0 }}>配置示例（后端环境变量 / .env.local）</h3>
        <pre style={pre}>{`CTX_SESSION_TURNS=40          # 会话最多保留多少轮
CTX_WINDOW_TURNS=6            # 会话详情 context_preview 的近窗轮数
CTX_RUNTIME_TURNS=2           # 生成器 5 条路径的近窗轮数
CTX_MIN_TURNS=1
CTX_MAX_TURNS=40              # 请求级 context_window 的夹取上下限
CTX_COMPRESS_ENABLED=1        # 默认 0；开启后超窗历史才进摘要
CTX_COMPRESS_PROVIDER=extractive   # 或 llm
CTX_COMPRESS_MAX_CHARS=1200
CTX_COMPRESS_TURN_CHARS=160
CTX_COMPRESS_MAX_TOKENS=300`}</pre>
        <div style={{ ...small, marginTop: 8 }}>
          单次请求也可在 POST /agent/invoke 里带 <code>context_window</code> 覆盖，仅对该次生成生效，同步与流式同口径。
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3 style={{ marginTop: 0 }}>能力与边界</h3>
        <ul style={{ ...small, lineHeight: 1.9, paddingLeft: 20, margin: 0 }}>
          <li><b>默认零行为变更</b>：三个默认值 40 / 6 / 2 与改动前逐一对齐，压缩默认关；关闭时 build_context 输出逐字节一致。</li>
          <li><b>摘要只作线索</b>：extractive 是确定性摘录（助手轮只取首句、去重、封顶），不是理解后的改写；注入文本显式标注可能不完整。</li>
          <li><b>LLM 通道诚实降级</b>：无真模型、模型报错、返回空文本一律回落 extractive，并带出 fallback + error；stub 文本绝不冒充摘要。</li>
          <li><b>两套口径分开</b>：生成器近窗（runtime，默认 2 轮）与会话详情预览（默认 6 轮）独立配置，改一处不会悄悄改另一处。</li>
          <li><b>预览只读</b>：GET 预览带会话归属校验（非本人 403 / 不存在 404）；POST 自定义试算不读写任何会话，轮数与单轮字数均设上限。</li>
          <li><b>估算不是计量</b>：estimated_tokens 按中文字符粗估，仅供判断窗口宽窄；真实 token 以 usage 落账为准。</li>
        </ul>
      </div>
    </div>
  );
}
