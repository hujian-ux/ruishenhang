import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** P1-10 · 图像理解台（#305）。
 *  可插拔视觉通道 + 配置开关：先上传图片拿 file_id，再调 /vision/analyze 解读。
 *  通道未启用/调用失败时后端返回 ok=false、text 为空，这里如实显示「未产出结论」，
 *  绝不把占位文案当识别结果展示。识别结论标注为「模型解读」，与原始事实区分。 */
type Any = Record<string, any>;
type Picked = { file: File; url: string };

const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--card)" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box" };
const ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,.bmp";

function kb(n: number) {
  if (!n) return "0 B";
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function VisionPage() {
  const [status, setStatus] = useState<Any>({});
  const [picked, setPicked] = useState<Picked[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<Any | null>(null);
  const [uploaded, setUploaded] = useState<any[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await api.getVisionStatus();
      setStatus(r.data || {});
    } catch (ex: any) { setErr(ex.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { picked.forEach((p) => URL.revokeObjectURL(p.url)); }, [picked]);

  const onPick = (list: FileList | null) => {
    const max = status.max_images || 4;
    const next = Array.from(list || [])
      .filter((f) => /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name))
      .slice(0, max)
      .map((file) => ({ file, url: URL.createObjectURL(file) }));
    setPicked(next);
    setResult(null);
    setUploaded([]);
  };

  const run = async () => {
    if (!picked.length || !status.ready) return;
    setBusy(true); setErr(""); setResult(null); setUploaded([]);
    try {
      const recs: any[] = [];
      for (const p of picked) {
        const r = await api.uploadAgentFile(p.file);
        recs.push(r.data);
      }
      setUploaded(recs);
      const a = await api.analyzeVision({ file_ids: recs.map((r) => r.file_id), prompt });
      setResult(a.data || {});
    } catch (ex: any) { setErr(ex.message); }
    setBusy(false);
  };

  const ready = !!status.ready;
  const imgs: any[] = result?.images || [];
  const errors: any[] = result?.errors || [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>图像理解台 · P1-10</h2>
          <div style={small}>可插拔视觉通道：图片走独立通道解读，结论以「模型解读」并入对话上下文；通道不可用如实降级，不伪造结论。</div>
        </div>
        <button className="btn" onClick={load}>刷新状态</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>{err}</div>}

      <div className="grid4">
        <div className="kpi"><span>通道状态</span>
          <b style={{ color: ready ? "var(--ok)" : "var(--warn)" }}>{ready ? "已就绪" : "未就绪"}</b>
          <i>{status.enabled ? "VISION_ENABLED=1" : "VISION_ENABLED=0"}</i></div>
        <div className="kpi"><span>当前通道</span><b style={{ fontSize: 18 }}>{status.provider || "—"}</b>
          <i>已注册 {(status.registered_providers || []).join(" / ") || "—"}</i></div>
        <div className="kpi"><span>视觉模型</span><b style={{ fontSize: 18 }}>{status.model || "未配置"}</b>
          <i>超时 {status.timeout_s ?? 0}s</i></div>
        <div className="kpi"><span>单次限制</span><b>{status.max_images ?? 0}<span style={{ fontSize: 13 }}> 张</span></b>
          <i>{kb(status.max_image_bytes || 0)} / 张 · {(status.allowed_ext || []).join("/")}</i></div>
      </div>

      {!ready && (
        <div className="card" style={{ marginTop: 14, borderColor: "var(--warn)" }}>
          <h3 style={{ marginTop: 0 }}>通道未启用 · 上传与解读已停用</h3>
          <div style={{ ...small, marginBottom: 8 }}>
            {status.reason || "尚未配置视觉通道"}。默认配置下上传图片会返回 415，避免出现「传上去了却永远不会被解读」的假成功。
            在后端环境变量中开启后刷新本页：
          </div>
          <pre style={{ margin: 0, padding: 10, background: "var(--bg)", borderRadius: 6, fontSize: 12, overflowX: "auto" }}>
{`VISION_ENABLED=1
VISION_PROVIDER=openai_compatible
VISION_MODEL=<视觉模型名>
VISION_BASE_URL=<OpenAI 兼容 base_url>
VISION_API_KEY=<密钥，留空则复用 LLM_API_KEY>`}
          </pre>
          <div style={{ ...small, marginTop: 8 }}>
            也可用 register_provider / set_provider 注入私有通道（测试与私有化部署用），无需改动本页代码。
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h3>① 选择图片</h3>
        <div className="row" style={{ alignItems: "center", gap: 10 }}>
          <input type="file" accept={ACCEPT} multiple disabled={!ready}
            onChange={(e) => onPick(e.target.files)} style={{ fontSize: 13 }} />
          <span style={small}>已选 {picked.length} / {status.max_images ?? 0} 张</span>
        </div>
        {picked.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 8, marginTop: 10 }}>
            {picked.map((p) => (
              <div key={p.url} style={{ ...box, padding: 8 }}>
                <img src={p.url} alt={p.file.name}
                  style={{ width: "100%", height: 90, objectFit: "contain", background: "var(--bg)", borderRadius: 4 }} />
                <div style={{ ...small, marginTop: 6, wordBreak: "break-all" }}>{p.file.name}</div>
                <div style={small}>{kb(p.file.size)}</div>
              </div>
            ))}
          </div>
        )}
        <div style={{ marginTop: 12, ...small }}>解读要求（可选，留空用「客观描述可核实内容」默认提示词）</div>
        <textarea className="field" rows={2} style={input} maxLength={2000}
          placeholder="例如：读出表格中的月份、产量与同比，看不清的字段标注「无法辨认」"
          value={prompt} onChange={(e) => setPrompt(e.target.value)} disabled={!ready} />
        <div className="row" style={{ marginTop: 10, alignItems: "center", gap: 10 }}>
          <button className="btn primary" disabled={!ready || busy || !picked.length} onClick={run}>
            {busy ? "上传并解读中…" : "上传并解读"}
          </button>
          {uploaded.length > 0 && <span style={small}>已入库 {uploaded.length} 份图片（file_id 可直接用于 /agent/invoke）</span>}
        </div>
      </div>

      {result && (
        <div className="card" style={{ marginTop: 14, borderColor: result.ok ? "var(--ok)" : "var(--warn)" }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>② 识别结果</h3>
            <span className="tag" style={{ color: result.ok ? "var(--ok)" : "var(--warn)",
              borderColor: result.ok ? "var(--ok)" : "var(--warn)" }}>
              {result.ok ? "成功" : "未产出结论"}
            </span>
          </div>
          <div style={{ ...small, marginTop: 6 }}>
            通道 {result.provider || "—"} · 模型 {result.model || "—"} ·
            真模型 {result.real ? "是" : "否"} · 耗时 {result.elapsed_ms ?? 0} ms ·
            token {(result.usage?.prompt_tokens ?? 0) + (result.usage?.completion_tokens ?? 0)}
          </div>

          {result.ok ? (
            <div style={{ ...box, marginTop: 10, whiteSpace: "pre-wrap", lineHeight: 1.7, fontSize: 13 }}>
              {result.text}
            </div>
          ) : (
            <div style={{ ...box, marginTop: 10, borderColor: "var(--warn)", color: "var(--warn)" }}>
              <b>未产出结论</b>：{result.error || result.reason || "视觉通道不可用"}。
              下方不会展示任何猜测内容，主对话会被明确告知「图片在、但没读出来」。
            </div>
          )}

          {imgs.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={small}>参与解读的图片</div>
              <table className="tbl" style={{ width: "100%", marginTop: 4 }}>
                <thead><tr><th>文件名</th><th>格式</th><th>大小</th><th>file_id</th></tr></thead>
                <tbody>
                  {imgs.map((i: Any) => (
                    <tr key={i.file_id}>
                      <td>{i.filename}</td><td>{i.format}</td><td>{kb(i.size)}</td>
                      <td style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{i.file_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {errors.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={small}>被跳过的文件</div>
              {errors.map((e: Any, idx: number) => (
                <div key={idx} style={{ ...small, color: "var(--warn)" }}>
                  {e.filename || e.file_id}：{e.error}
                </div>
              ))}
            </div>
          )}
          {!!result.context && (
            <div style={{ marginTop: 12 }}>
              <div style={small}>将并入主对话 file_context 的文本（可人工核对口径）</div>
              <pre style={{ ...box, marginTop: 4, whiteSpace: "pre-wrap", fontSize: 12, lineHeight: 1.6 }}>
                {result.context}
              </pre>
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h3>与主对话的关系</h3>
        <div style={{ ...small, lineHeight: 1.8 }}>
          在对话里把图片的 file_id 一并传进 /agent/invoke 或 /agent/stream：后端会把图片从表格解析链中摘出，
          交由视觉通道解读，再把结论（或「未产出结论」的缺料提示）并入附件上下文；<b>图片不进表格解析器</b>，
          <b>带图轮次不复用语义缓存</b>，响应里新增 vision 字段记录通道、模型与降级原因。
          默认 VISION_ENABLED=0 时全链路零行为变更。
        </div>
      </div>
    </div>
  );
}
