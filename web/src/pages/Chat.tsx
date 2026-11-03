import { useEffect, useRef, useState } from "react";
import { api, downloadAgentFile } from "../api/client";

type Attachment = {
  file_id: string;
  filename: string;
  size: number;
  meta?: { parse_ok?: boolean; parse_error?: string; sheets?: number; rows?: number; store?: string };
  preview?: { ok: boolean; sheet_count?: number; total_rows?: number };
};

type Turn = {
  role: "user" | "assistant";
  content: string;
  files?: string[];        // 用户轮：带哪些附件
  artifacts?: any[];       // 助手轮：本次产出物
  runId?: string;
  skillApplied?: boolean;
};

const ACCEPT = ".xlsx,.xlsm,.csv,.tsv,.txt,.md,.markdown,.json";

function kb(n: number) {
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

export function ChatPage() {
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [memory, setMemory] = useState<any>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dlBusy, setDlBusy] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listAgents().then((r) => {
      const list = r.data.agents || [];
      setAgents(list);
      if (list.length) setAgentId(list[0].agent_id);
    }).catch(() => setAgents([]));
  }, []);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [turns]);

  const pickFiles = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setUploading(true); setErr("");
    try {
      const added: Attachment[] = [];
      for (const f of Array.from(files)) {
        const r = await api.uploadAgentFile(f);
        added.push(r.data);
      }
      setAttachments((a) => [...a, ...added]);
    } catch (e: any) {
      setErr(e.message || "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const send = async () => {
    const text = input.trim();
    if ((!text && !attachments.length) || !agentId || busy) return;
    const fileIds = attachments.map((a) => a.file_id);
    const fileNames = attachments.map((a) => a.filename);
    setBusy(true); setErr(""); setInput("");
    setTurns((t) => [...t, { role: "user", content: text || "（已上传附件，请按技能包执行）", files: fileNames }]);
    try {
      const r = await api.invokeAgent(agentId, text || "请按技能包处理附件", sessionId || undefined, fileIds);
      const d = r.data;
      setSessionId(d.session.session_id);
      setTurns((t) => [...t, {
        role: "assistant", content: d.answer,
        artifacts: d.artifacts || [], runId: d.run_id,
        skillApplied: d.agent?.skill_applied,
      }]);
      setAttachments([]);
      api.getSession(d.session.session_id).then((m) => setMemory(m.data)).catch(() => {});
    } catch (e: any) {
      setErr(e.message || "调用失败");
    } finally {
      setBusy(false);
    }
  };

  const download = async (a: any) => {
    setDlBusy(a.file_id); setErr("");
    try {
      await downloadAgentFile(a.file_id, a.filename);
    } catch (e: any) {
      setErr(e.message || "下载失败");
    } finally {
      setDlBusy("");
    }
  };

  const newSession = () => {
    setSessionId(null); setTurns([]); setMemory(null); setErr(""); setAttachments([]);
  };

  const canSend = !!agentId && !busy && (!!input.trim() || attachments.length > 0);

  return (
    <div className="card">
      <h3>智能体对话（M4 多轮记忆 · M16 文件进出）</h3>
      <div className="row" style={{ alignItems: "center" }}>
        <select value={agentId} onChange={(e) => { setAgentId(e.target.value); newSession(); }}
                style={{ flex: 1, padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 8, fontSize: 13 }}>
          {agents.length === 0 && <option value="">（无已注册智能体，请先到「选型/建智能体」注册）</option>}
          {agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.name} · {a.agent_id} · {a.adapter_type}</option>)}
        </select>
        <button className="btn" onClick={newSession}>新会话</button>
      </div>
      <div className="hint" style={{ marginTop: 6 }}>
        会话：{sessionId ? <span className="tag">{sessionId}</span> : "（首轮发送后自动创建）"}
        {memory && <> · 已记 {memory.turn_count} 轮 · 仅你本人可见</>}
      </div>

      <div ref={boxRef} style={{ marginTop: 10, maxHeight: 360, overflowY: "auto", padding: "4px 2px" }}>
        {turns.length === 0 && (
          <div className="hint">
            技能包用法：点「＋ 附件」上传台账 xlsx → 发送执行要求 → 直接下载产出 xlsx。
            可试「经营复盘」配一份费用台账。
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} style={{ display: "flex", justifyContent: t.role === "user" ? "flex-end" : "flex-start", margin: "6px 0" }}>
            <div style={{
              maxWidth: "82%", padding: "8px 12px", borderRadius: 10, fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap",
              background: t.role === "user" ? "var(--accent)" : "#eef3fa", color: t.role === "user" ? "#fff" : "var(--ink)",
            }}>
              {t.content}
              {!!t.files?.length && (
                <div style={{ marginTop: 6, fontSize: 11.5, opacity: 0.9 }}>
                  📎 {t.files.join("、")}
                </div>
              )}
              {!!t.artifacts?.length && (
                <div style={{ marginTop: 8, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
                  <div style={{ fontSize: 11.5, color: "#4a5b6d", marginBottom: 4 }}>
                    产出物{t.skillApplied ? "（按技能包产出契约生成）" : ""}
                    {t.runId ? ` · ${t.runId}` : ""}
                  </div>
                  {t.artifacts.map((a) => (
                    <button key={a.file_id} className="btn" onClick={() => download(a)} disabled={dlBusy === a.file_id}
                            style={{ fontSize: 12, marginRight: 6, marginTop: 2 }}
                            title={`${a.filename} · ${kb(a.size)}`}>
                      {dlBusy === a.file_id ? "准备中…" : `⤓ ${a.filename}`}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="hint">执行中（解析附件 → 按技能包流程执行 → 生成产出物）…</div>}
        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
      </div>

      {attachments.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {attachments.map((a) => {
            const ok = a.meta?.parse_ok !== false;
            return (
              <span key={a.file_id} className="tag"
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 8px",
                      borderColor: ok ? "var(--line)" : "#e0a0a0",
                      background: ok ? undefined : "#fdf0f0",
                    }}
                    title={ok ? `${a.preview?.sheet_count ?? 0} 个工作表 · ${a.preview?.total_rows ?? 0} 行`
                              : a.meta?.parse_error || "解析失败"}>
                {ok ? "📎" : "⚠️"} {a.filename}
                <span style={{ opacity: 0.65 }}>
                  {ok ? `${a.meta?.rows ?? 0} 行 · ${kb(a.size)}` : "读不出来"}
                </span>
                <span style={{ cursor: "pointer", fontWeight: 700 }}
                      onClick={() => setAttachments((x) => x.filter((y) => y.file_id !== a.file_id))}>×</span>
              </span>
            );
          })}
        </div>
      )}

      <div className="row" style={{ marginTop: 8 }}>
        <input ref={fileRef} type="file" accept={ACCEPT} multiple style={{ display: "none" }}
               onChange={(e) => pickFiles(e.target.files)} />
        <button className="btn" onClick={() => fileRef.current?.click()} disabled={uploading || busy}
                title="上传 xlsx / csv / txt 作为技能包输入">
          {uploading ? "上传中…" : "＋ 附件"}
        </button>
        <input value={input} placeholder={agentId ? "输入执行要求，回车发送（可只发附件）" : "请先注册智能体"}
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") send(); }}
               disabled={!agentId || busy}
               style={{ flex: 1, padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 8, fontSize: 13 }} />
        <button className="btn" onClick={send} disabled={!canSend}>发送</button>
      </div>

      {memory && (
        <details style={{ marginTop: 10 }}>
          <summary className="hint" style={{ cursor: "pointer" }}>查看会话记忆（GET /agent/sessions）</summary>
          <pre style={{ background: "#0a2540", color: "#cfe0f5", padding: 10, borderRadius: 8, fontSize: 11.5, overflowX: "auto" }}>
{JSON.stringify(memory, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
