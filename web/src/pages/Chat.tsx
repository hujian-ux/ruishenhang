import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type Turn = { role: "user" | "assistant"; content: string };

export function ChatPage() {
  const [agents, setAgents] = useState<any[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [memory, setMemory] = useState<any>(null);
  const boxRef = useRef<HTMLDivElement>(null);

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

  const send = async () => {
    const text = input.trim();
    if (!text || !agentId || busy) return;
    setBusy(true); setErr(""); setInput("");
    setTurns((t) => [...t, { role: "user", content: text }]);
    try {
      const r = await api.invokeAgent(agentId, text, sessionId || undefined);
      const d = r.data;
      setSessionId(d.session.session_id);
      setTurns((t) => [...t, { role: "assistant", content: d.answer }]);
      // 后台刷新会话记忆摘要（轮次数/改写标记）
      api.getSession(d.session.session_id)
        .then((m) => setMemory(m.data))
        .catch(() => {});
    } catch (e: any) {
      setErr(e.message || "调用失败");
    } finally {
      setBusy(false);
    }
  };

  const newSession = () => { setSessionId(null); setTurns([]); setMemory(null); setErr(""); };

  return (
    <div className="card">
      <h3>智能体对话（M4 · 多轮编排 + 会话记忆）</h3>
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
      <div ref={boxRef} style={{ marginTop: 10, maxHeight: 340, overflowY: "auto", padding: "4px 2px" }}>
        {turns.length === 0 && <div className="hint">试试：先问「五一活动方案有哪些」，再问「预算多少」——第二轮会自动带上第一轮的记忆做检索改写。</div>}
        {turns.map((t, i) => (
          <div key={i} style={{ display: "flex", justifyContent: t.role === "user" ? "flex-end" : "flex-start", margin: "6px 0" }}>
            <div style={{
              maxWidth: "82%", padding: "8px 12px", borderRadius: 10, fontSize: 13, lineHeight: 1.5, whiteSpace: "pre-wrap",
              background: t.role === "user" ? "var(--accent)" : "#eef3fa", color: t.role === "user" ? "#fff" : "var(--ink)",
            }}>{t.content}</div>
          </div>
        ))}
        {busy && <div className="hint">思考中…</div>}
        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input value={input} placeholder={agentId ? "输入问题，回车发送" : "请先注册智能体"}
               onChange={(e) => setInput(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") send(); }}
               disabled={!agentId || busy}
               style={{ flex: 1, padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 8, fontSize: 13 }} />
        <button className="btn" onClick={send} disabled={!agentId || busy || !input.trim()}>发送</button>
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
