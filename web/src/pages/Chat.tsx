import { useEffect, useMemo, useRef, useState } from "react";
import { api, downloadAgentFile } from "../api/client";
import { RoleChip, RoleGrid, type Role } from "../components/RolePicker";
import { useRole } from "../role";

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
  files?: string[];
  artifacts?: any[];
  runId?: string;
  skillApplied?: boolean;
  llmReal?: boolean;
  llmError?: string;
};

export type ChatSeed = {
  sessionId?: string;
  agentId?: string;
  turns?: { role: "user" | "assistant"; content: string; artifacts?: any[]; llmReal?: boolean }[];
};

// 后端用 `[stub|model]` / `[第N轮|LLM生成]` 标记这一轮到底走没走真模型，
// 那是给日志和判真用的，不该出现在用户眼前。是否真跑由下方提示条说明。
function cleanAnswer(s: string) {
  return (s || "")
    .replace(/^\[stub[^\]]*\]\s*/i, "")
    .replace(/^\[第\d+轮\|LLM生成\]\s*/, "");
}

const ACCEPT = ".xlsx,.xlsm,.csv,.tsv,.txt,.md,.markdown,.json";

const FORMATS: { key: string; label: string; ext: string }[] = [
  { key: "xlsx", label: "Excel", ext: "XLSX" },
  { key: "docx", label: "Word", ext: "DOCX" },
  { key: "pptx", label: "PPT", ext: "PPTX" },
  { key: "pdf", label: "PDF", ext: "PDF" },
  { key: "html", label: "网页", ext: "HTML" },
];

const ICONS = ["◑", "◧", "◔", "◩", "◐", "◨"];

function kb(n: number) {
  if (!n) return "";
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

function extOf(name: string) {
  const e = (name || "").split(".").pop() || "file";
  return e.toLowerCase();
}

// 产出物命名是「技能包_源文件_20261105-1528.xlsx」，卡片上只留技能包名：
// 一次执行的五种格式主名相同，靠左侧格式徽标区分；完整名放 title。
function niceName(fn: string) {
  const base = (fn || "").replace(/\.[^.]+$/, "").replace(/_\d{8}-\d{4}$/, "");
  return base.split("_")[0] || base;
}

function greeting() {
  const h = new Date().getHours();
  if (h < 6) return "夜里好";
  if (h < 12) return "上午好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export function ChatPage({ seed, userName, onSession, onOpenSkills }: {
  seed: ChatSeed;
  userName: string;
  onSession: (sessionId: string, firstTitle?: string) => void;
  onOpenSkills?: () => void;
}) {
  const [agents, setAgents] = useState<any[]>([]);
  const [roleId, setRoleId] = useRole();
  const [roleList, setRoleList] = useState<Role[]>([]);
  const [ws, setWs] = useState<any>(null);
  const [pickRole, setPickRole] = useState(false);
  const [agentId, setAgentId] = useState(seed.agentId || "");
  const [sessionId, setSessionId] = useState<string | null>(seed.sessionId || null);
  const [turns, setTurns] = useState<Turn[]>(
    (seed.turns || []).map((t) => ({
      role: t.role, content: t.content, artifacts: t.artifacts, llmReal: t.llmReal,
    })),
  );
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dlBusy, setDlBusy] = useState("");
  // 打开历史对话时沿用当时选的格式，否则会出现"上次出了 5 种，这次突然只剩 Excel"
  const [formats, setFormats] = useState<string[]>(() => {
    const seen = Array.from(new Set(
      (seed.turns || []).flatMap((t) => t.artifacts || [])
        .map((a: any) => a?.meta?.format).filter(Boolean),
    )) as string[];
    return seen.length ? seen : ["xlsx"];
  });
  const [skillPop, setSkillPop] = useState(false);
  const [fmtPop, setFmtPop] = useState(false);
  // 用户手动改过格式后，选技能包就不再替他改（否则会"我明明选了 PDF，它给我改回 Word"）
  const [fmtTouched, setFmtTouched] = useState(false);
  const [q, setQ] = useState("");

  const boxRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // 技能清单跟着岗位走：选完岗位只拉本岗 + 公共库 + 通用助手。
  // 之前拉全量 115 个，用户在「换一个」里能看到别的岗位的包，等于岗位没边界。
  useEffect(() => {
    let alive = true;
    api.listAgents(roleId || undefined).then((r) => {
      if (!alive) return;
      const list = (r.data.agents || []).filter((a: any) => a.status === "published");
      setAgents(list);
      setAgentId((cur) => {
        // 当前选的技能不在新岗位范围内 → 退回通用助手，避免"选了财务却在跑选品"
        if (cur && list.some((a: any) => a.agent_id === cur)) return cur;
        return list.find((a: any) => a.builtin)?.agent_id || list[0]?.agent_id || "";
      });
    }).catch(() => { if (alive) setAgents([]); });
    return () => { alive = false; };
  }, [roleId]);

  // 岗位目录只拉一次：11 个岗位 + 各自数量，用来渲染选择器
  useEffect(() => {
    api.getRoles()
      .then((r) => setRoleList(r.data.roles || []))
      .catch(() => setRoleList([]));
  }, []);

  // 岗位一变就换一套推荐位——这是"选完岗位，常用 AI 先配进来"的落点
  useEffect(() => {
    if (!roleId) { setWs(null); return; }
    let alive = true;
    api.getRoleWorkspace(roleId)
      .then((r) => { if (alive) setWs(r.data); })
      .catch(() => { if (alive) setWs(null); });
    return () => { alive = false; };
  }, [roleId]);

  const activeRole = useMemo(() => roleList.find((r) => r.id === roleId), [roleList, roleId]);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  useEffect(() => {
    if (taRef.current) {
      taRef.current.style.height = "auto";
      taRef.current.style.height = Math.min(taRef.current.scrollHeight, 180) + "px";
    }
  }, [input]);

  const agent = useMemo(() => agents.find((a) => a.agent_id === agentId), [agents, agentId]);
  const filtered = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return agents;
    return agents.filter((a) => `${a.name}${a.description || ""}`.toLowerCase().includes(k));
  }, [agents, q]);

  // 推荐位只用「该岗位的核心技能」；不再拿全量列表补位——那会把别的岗位的包摆到眼前，
  // 选财务却推荐"AI选品机会发现"，用户会以为这系统乱推。
  const featured = useMemo(() => {
    const names: string[] = (ws?.core || []).map((a: any) => a.name);
    const pool = agents.filter((a) => !a.builtin && !a.public);
    const out: any[] = [];
    for (const n of names) {
      const hit = pool.find((a) => a.name === n);
      if (hit && !out.includes(hit)) out.push(hit);
    }
    if (!out.length) out.push(...pool.slice(0, 4));
    return out.slice(0, 4);
  }, [agents, ws]);

  // 公共库快捷入口：优先露"每天都要用"的三个（文案/PPT/表格）+ 会议纪要，
  // 不按入库顺序取前 4 个——那样会摆出"制度起草"，而表格反而看不见。
  const pubPicks = useMemo(() => {
    const src: any[] = (ws?.public?.length ? ws.public : agents.filter((a) => a.public));
    const first = ["文案生成", "PPT 制作", "表格制作", "会议纪要"];
    const out = first.map((n) => src.find((a) => a.name === n)).filter(Boolean) as any[];
    for (const a of src) {
      if (out.length >= 4) break;
      if (!out.some((x) => x.agent_id === a.agent_id)) out.push(a);
    }
    return out;
  }, [agents, ws]);

  // 点卡片必须立刻看得见结果：选中态 + 光标进输入框。之前只 setAgentId，
  // 页面上唯一变化是输入区按钮那串小字，用户会以为点了没反应。
  const pickSkill = (id: string) => {
    setAgentId(id);
    // 技能包自带建议产出（表格类出 Excel、PPT 类出 PPTX）。用户没手动改过就跟着走，
    // 省掉"选完还要再去设一次格式"这一步。
    const hit = agents.find((a) => a.agent_id === id);
    if (!fmtTouched && hit?.default_format) setFormats([hit.default_format]);
    setSkillPop(false);
    setQ("");
    requestAnimationFrame(() => taRef.current?.focus());
  };

  const builtinId = useMemo(
    () => agents.find((a: any) => a.builtin)?.agent_id || agents[0]?.agent_id || "",
    [agents]
  );

  // 取消选中：回到内置通用助手，避免"选错了没法退"
  const clearSkill = () => {
    setAgentId(builtinId);
    requestAnimationFrame(() => taRef.current?.focus());
  };

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
    setBusy(true); setErr("");
    setInput("");
    setTurns((t) => [...t, { role: "user", content: text || "请按技能包处理这份附件", files: fileNames }]);
    try {
      const r = await api.invokeAgent(agentId, text || "请按技能包处理附件",
                                      sessionId || undefined, fileIds, formats);
      const d = r.data;
      setSessionId(d.session.session_id);
      setTurns((t) => [...t, {
        role: "assistant", content: d.answer,
        artifacts: d.artifacts || [], runId: d.run_id,
        skillApplied: d.agent?.skill_applied,
        llmReal: d.llm?.real, llmError: d.llm?.error,
      }]);
      setAttachments([]);
      onSession(d.session.session_id, (text || fileNames[0] || "").slice(0, 26));
    } catch (e: any) {
      setErr(e.message || "执行失败，请重试");
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

  const empty = turns.length === 0;

  return (
    <div className="chat-shell">
      <div className="thread-scroll" ref={boxRef}
           style={empty ? { display: "flex", flexDirection: "column", justifyContent: "center" } : undefined}>
        {empty ? (
          <div className="welcome">
            <div className="hello" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              {greeting()}，{userName}
              {activeRole && !pickRole && (
                <RoleChip role={activeRole} onClick={() => setPickRole(true)} />
              )}
            </div>
            <h1>今天想让 AI <em>帮你做什么</em>？</h1>

            {roleList.length > 0 && (!activeRole || pickRole) ? (
              <div className="role-pick">
                <h4>{activeRole ? "换个岗位" : "先认一下你的岗位"}</h4>
                <div className="hint">
                  {activeRole
                    ? "换岗位只换推荐技能，历史对话不受影响。"
                    : "选完我把这个岗位最常用的 4 个 AI 直接放到下面，以后打开就在。不确定就随便选，随时能改。"}
                </div>
                <RoleGrid roles={roleList} value={roleId}
                          onChange={(id) => { setRoleId(id); setPickRole(false); }} />
              </div>
            ) : activeRole ? (
              <p className="lead">
                {activeRole.name}岗已配好 {ws?.core?.length || featured.length} 个常用技能：
                选一张卡，再说一句要处理什么，就开工。公共库的文案、PPT、表格不分岗位，随时可用。
              </p>
            ) : null}

            {activeRole && !pickRole && (
              <>
                <div className="w-cards">
                  {featured.map((a, i) => {
                    const on = agentId === a.agent_id;
                    return (
                      <div key={a.agent_id} className={"w-card" + (on ? " on" : "")}
                           role="button" tabIndex={0}
                           aria-pressed={on}
                           onClick={() => pickSkill(a.agent_id)}
                           onKeyDown={(e) => {
                             if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickSkill(a.agent_id); }
                           }}>
                        <div className="w-ico">{ICONS[i % ICONS.length]}</div>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <b>{a.name}</b>
                          <span>{a.description || "按技能包流程执行"}</span>
                        </div>
                        {on && <span className="w-on">已选</span>}
                      </div>
                    );
                  })}
                </div>

                <div className="w-pub">
                  <span className="w-pub-t">公共库</span>
                  {pubPicks.map((a: any) => (
                    <span key={a.agent_id}
                          className={"w-pub-i" + (agentId === a.agent_id ? " on" : "")}
                          role="button" tabIndex={0}
                          onClick={() => pickSkill(a.agent_id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickSkill(a.agent_id); }
                          }}>
                      {a.name}
                    </span>
                  ))}
                  <span className="w-pub-more"
                        onClick={() => (onOpenSkills ? onOpenSkills() : setSkillPop(true))}>
                    全部 {ws?.public?.length || pubPicks.length} 个 →
                  </span>
                </div>

                <span className="w-all" onClick={() => (onOpenSkills ? onOpenSkills() : setSkillPop(true))}>
                  浏览{activeRole.name}全部 {ws?.count ?? 0} 个技能包 →
                </span>
              </>
            )}

            {!activeRole && (
              <span className="w-all" onClick={() => (onOpenSkills ? onOpenSkills() : setSkillPop(true))}>
                不选岗位也能用公共库（文案 / PPT / 表格），先看看 →
              </span>
            )}
          </div>
        ) : (
          <div className="thread">
            {turns.map((t, i) => (
              <div key={i} className={"msg " + t.role}>
                {t.role === "user" ? (
                  <>
                    <div className="who">{userName}</div>
                    <div className="bubble">
                      {t.content}
                      {!!t.files?.length && (
                        <div style={{ marginTop: 6, fontSize: 11.5, opacity: 0.7 }}>
                          {t.files.map((f) => <div key={f}>📎 {f}</div>)}
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="who">
                      <span style={{ width: 16, height: 16, borderRadius: 5, background: "var(--ink)",
                                     color: "#fff", fontSize: 9, display: "inline-flex",
                                     alignItems: "center", justifyContent: "center" }}>AI</span>
                      {agent?.name || "AI 工作台"}
                    </div>
                    <div className="body">{cleanAnswer(t.content)}</div>
                    {!!t.artifacts?.length && (
                      <div className="artifacts">
                        {t.artifacts.map((a: any) => {
                          const ext = extOf(a.filename);
                          return (
                            <div key={a.file_id} className="artifact" onClick={() => download(a)}
                                 title={`${a.filename} · ${kb(a.size)}`}>
                              <div className={"ext " + ext}>{dlBusy === a.file_id ? "…" : ext.toUpperCase()}</div>
                              <div className="meta">
                                <b>{niceName(a.filename)}</b>
                                <span>{dlBusy === a.file_id ? "准备中…"
                                  : `${ext.toUpperCase()} · ${kb(a.size)} · 点击下载`}</span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {t.llmReal === false && (
                      <div className="notice">
                        本次未走真模型{t.llmError ? `（${t.llmError}）` : ""}，内容仅供参考，不要对外使用。
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
            {busy && (
              <div className="msg assistant">
                <div className="who">
                  <span style={{ width: 16, height: 16, borderRadius: 5, background: "var(--ink)",
                                 color: "#fff", fontSize: 9, display: "inline-flex",
                                 alignItems: "center", justifyContent: "center" }}>AI</span>
                  {agent?.name || "AI 工作台"}
                </div>
                <div className="pending">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                  正在按技能包流程执行…
                </div>
              </div>
            )}
            {err && <div className="err">{err}</div>}
          </div>
        )}
      </div>

      <div className="composer-wrap">
        <div className="composer" style={{ position: "relative" }}>
          {!!attachments.length && (
            <div className="chips">
              {attachments.map((a) => {
                const ok = a.meta?.parse_ok !== false;
                return (
                  <span key={a.file_id} className={"chip" + (ok ? "" : " bad")}
                        title={ok ? `${a.preview?.sheet_count ?? 0} 个工作表 · ${a.preview?.total_rows ?? 0} 行`
                                  : a.meta?.parse_error || "解析失败"}>
                    {ok ? "📎" : "⚠"} {a.filename}
                    <span style={{ opacity: 0.6 }}>{ok ? kb(a.size) : "读不出来"}</span>
                    <span className="x"
                          onClick={() => setAttachments((x) => x.filter((y) => y.file_id !== a.file_id))}>×</span>
                  </span>
                );
              })}
            </div>
          )}

          {agent && !agent.builtin && (
            <div className="picked">
              <span className="p-ico">◈</span>
              <b>{agent.name}</b>
              <span className="p-kind">
                {agent.adapter_type === "rule" ? "按规则跑"
                  : agent.adapter_type === "llm_retrieval" ? "先查制度再答" : "按流程生成"}
              </span>
              <span className="p-swap" onClick={() => { setSkillPop(true); setFmtPop(false); }}>换一个</span>
              <span className="p-x" onClick={clearSkill} title="取消，改用通用助手">×</span>
            </div>
          )}

          <textarea
            ref={taRef}
            rows={1}
            value={input}
            placeholder={agent && !agent.builtin
              ? `说明这次要处理什么，${agent.name}会按流程执行`
              : empty ? "描述你要做的事，或先上传文件…" : "继续追问，或上传新文件"}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            disabled={busy}
          />

          <div className="ctools">
            <input ref={fileRef} type="file" accept={ACCEPT} multiple style={{ display: "none" }}
                   onChange={(e) => pickFiles(e.target.files)} />
            <button className="tool" onClick={() => fileRef.current?.click()} disabled={uploading || busy}
                    title="上传 xlsx / csv / 文本作为输入">
              <span style={{ fontSize: 15, lineHeight: 1 }}>＋</span>{uploading ? "上传中" : "附件"}
            </button>

            <button className={"tool" + (skillPop ? " on" : "")} onClick={() => { setSkillPop((v) => !v); setFmtPop(false); }}
                    title="选择技能包">
              ◈ {agent?.name ? (agent.name.length > 10 ? agent.name.slice(0, 10) + "…" : agent.name) : "选择技能包"}
            </button>

            <button className={"tool" + (fmtPop ? " on" : "")} onClick={() => { setFmtPop((v) => !v); setSkillPop(false); }}
                    title={formats.map((f) => FORMATS.find((x) => x.key === f)?.label).join(" / ")}>
              ⤓ {formats.length > 2
                ? `${formats.length} 种格式`
                : formats.map((f) => FORMATS.find((x) => x.key === f)?.label).join(" / ")}
            </button>

            <button className="send" onClick={send}
                    disabled={!agentId || busy || (!input.trim() && !attachments.length)}
                    title="发送（Enter）">
              {busy ? "·" : "↑"}
            </button>
          </div>

          {skillPop && (
            <div className="pop" onClick={(e) => e.stopPropagation()}>
              <div className="pop-in">
                <input autoFocus placeholder="搜索技能包…" value={q} onChange={(e) => setQ(e.target.value)} />
                <div className="hint" style={{ padding: "2px 6px 6px", fontSize: 11.5 }}>
                  {activeRole
                    ? `只列${activeRole.name}岗 + 公共库，别的岗位不在这里`
                    : "还没选岗位，先看公共库"}
                </div>
                <div className="pop-list">
                  {filtered.length === 0 && <div className="pop-empty">没有匹配的技能包</div>}
                  {filtered.map((a) => (
                    <div key={a.agent_id} className="pop-item"
                         onClick={() => pickSkill(a.agent_id)}>
                      <b>{a.name}</b>
                      {a.public && <span className="pop-pub">公共</span>}
                      <span>{a.adapter_type === "llm_retrieval" ? "查制度" : a.adapter_type === "rule" ? "规则" : "生成"}</span>
                      {a.agent_id === agentId && <span style={{ color: "var(--blue)" }}>✓</span>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {fmtPop && (
            <div className="pop" style={{ left: "auto", right: 8, width: 220 }}>
              <div className="pop-in">
                <div className="hint" style={{ padding: "2px 6px 4px" }}>产出格式（可多选，不多花 token）</div>
                {FORMATS.map((f) => {
                  const on = formats.includes(f.key);
                  return (
                    <div key={f.key} className="fmt-item"
                         onClick={() => { setFmtTouched(true); setFormats((s) => (
                           s.includes(f.key) ? (s.length > 1 ? s.filter((x) => x !== f.key) : s)
                                             : [...s, f.key])); }}>
                      <span style={{ width: 14, color: on ? "var(--blue)" : "#c9c9c2" }}>{on ? "✓" : "○"}</span>
                      {f.label}
                      <span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--muted)", fontFamily: "var(--mono)" }}>
                        {f.ext}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
        <div className="foot-note">
          技能包会按自己的流程执行，产出物可直接下载。对话仅你本人可见。
        </div>
      </div>
    </div>
  );
}
