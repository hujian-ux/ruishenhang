import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, downloadAgentFile, fetchAgentFile } from "../api/client";
import { RoleChip, RoleGrid, type Role } from "../components/RolePicker";
import { useRole } from "../role";

type Attachment = {
  file_id: string;
  filename: string;
  size: number;
  meta?: { parse_ok?: boolean; parse_error?: string; sheets?: number; rows?: number; store?: string };
  preview?: { ok: boolean; sheet_count?: number; total_rows?: number };
};

// P0 收尾1：工具循环流式时，把每一步「调用 / 结果 / 转审批」实时展示在气泡内
type ToolStep = {
  tool: string;
  args?: any;
  ok?: boolean;        // 执行结果（tool_result 后才有）
  content?: string;    // 结果摘要
  escalated?: boolean; // 高危操作转审批
  ticketId?: string;
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
  // M26：公网来源与内部引用分开显示，刷新历史会话后仍在
  webSources?: { title: string; url: string; source?: string; published_at?: string }[];
  webSearch?: { mode?: string; degraded?: boolean; provider?: string; error?: string } | null;
  // M52：这一轮自动挂了哪些包内样例（让用户知道结论是基于什么数据算的）
  samplesUsed?: string[];
  // P0 收尾1：本次流式过程中实际发生的工具调用步骤
  toolSteps?: ToolStep[];
};

export type ChatSeed = {
  sessionId?: string;
  agentId?: string;
  turns?: { role: "user" | "assistant"; content: string; artifacts?: any[]; llmReal?: boolean;
            webSources?: Turn["webSources"]; webSearch?: Turn["webSearch"] }[];
};

// 后端用 `[stub|model]` / `[第N轮|LLM生成]` 标记这一轮到底走没走真模型，
// 那是给日志和判真用的，不该出现在用户眼前。是否真跑由下方提示条说明。
function cleanAnswer(s: string) {
  return (s || "")
    .replace(/^\[stub[^\]]*\]\s*/i, "")
    .replace(/^\[第\d+轮\|LLM生成\]\s*/, "");
}

// P0 收尾1：工具循环期间模型会在答案里夹带 ```tool_call{...}``` 块（给运行时解析用），
// 不让它原样显示给用户；前端只把工具调用渲染成结构化步骤卡片。
function stripToolBlocks(s: string) {
  return (s || "")
    .replace(/```tool_call\s*[\s\S]*?```/g, "")
    .replace(/<tool_call>[\s\S]*?<\/tool_call>/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function summarizeArgs(args: any) {
  if (!args) return "";
  try {
    const j = JSON.stringify(args);
    return j.length > 160 ? j.slice(0, 160) + "…" : j;
  } catch {
    return String(args).slice(0, 160);
  }
}

const ACCEPT = ".xlsx,.xlsm,.csv,.tsv,.txt,.md,.markdown,.json";

const FORMATS: { key: string; label: string; ext: string }[] = [
  { key: "html", label: "网页", ext: "HTML" },
  { key: "xlsx", label: "Excel", ext: "XLSX" },
  { key: "docx", label: "Word", ext: "DOCX" },
  { key: "pptx", label: "PPT", ext: "PPTX" },
  { key: "pdf", label: "PDF", ext: "PDF" },
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
      webSources: t.webSources, webSearch: t.webSearch,
    })),
  );
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dlBusy, setDlBusy] = useState("");
  // 右侧看板：点产出物卡片直接内联预览（HTML/PDF），不用先下载
  const [preview, setPreview] = useState<{ a: any; ext: string; url: string } | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const openPreview = useCallback(async (a: any) => {
    setDlBusy(a.file_id); setErr("");
    try {
      const blob = await fetchAgentFile(a.file_id);
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      setPreview({ a, ext: extOf(a.filename), url });
    } catch (e: any) {
      setErr(e.message || "预览加载失败");
    } finally {
      setDlBusy("");
    }
  }, []);
  const closePreview = useCallback(() => {
    if (previewUrlRef.current) { URL.revokeObjectURL(previewUrlRef.current); previewUrlRef.current = null; }
    setPreview(null);
  }, []);

  // P1-7 制品版本链：每张卡片可展开的历史版本面板（按 artifact_key 区分）
  const [versOpen, setVersOpen] = useState<string | null>(null);
  const [versData, setVersData] = useState<Record<string, any>>({});
  const [versBusy, setVersBusy] = useState("");

  const toggleVersions = useCallback(async (a: any) => {
    const k = a?.artifact_key;
    if (!k) return;
    if (versOpen === k) { setVersOpen(null); return; }
    setVersOpen(k); setErr("");
    if (!versData[k]) {
      try {
        const r = await api.listArtifactVersions(k);
        setVersData((d) => ({ ...d, [k]: r.data }));
      } catch (e: any) { setErr(e?.message || "拉取版本失败"); }
    }
  }, [versOpen, versData]);

  // 非破坏式回滚：把历史版本复制成最新，旧版本保留可再回滚。回滚后把该轮卡片与版本列表都刷成最新。
  const rollbackVersion = useCallback(async (a: any, version: number) => {
    const k = a?.artifact_key;
    if (!k) return;
    setVersBusy(`${k}:${version}`); setErr("");
    try {
      const r = await api.rollbackArtifact(k, version);
      const nr = r.data.artifact;
      setTurns((ts) => ts.map((t) => (t.role === "assistant" && t.artifacts
        ? { ...t, artifacts: (t.artifacts as any[]).map((x: any) =>
            x.artifact_key === k
              ? { ...x, file_id: nr.file_id, version: nr.version, download_url: nr.download_url,
                  filename: nr.filename, size: nr.size }
              : x) }
        : t)));
      const rv = await api.listArtifactVersions(k);
      setVersData((d) => ({ ...d, [k]: rv.data }));
    } catch (e: any) { setErr(e?.message || "回滚失败"); }
    finally { setVersBusy(""); }
  }, []);
  // 打开历史对话时沿用当时选的格式，否则会出现"上次出了 5 种，这次突然只剩 Excel"
  const [formats, setFormats] = useState<string[]>(() => {
    const seen = Array.from(new Set(
      (seed.turns || []).flatMap((t) => t.artifacts || [])
        .map((a: any) => a?.meta?.format).filter(Boolean),
    )) as string[];
    return seen.length ? seen : ["html"];
  });
  const [skillPop, setSkillPop] = useState(false);
  const [fmtPop, setFmtPop] = useState(false);
  // 用户手动改过格式后，选技能包就不再替他改（否则会"我明明选了 PDF，它给我改回 Word"）
  const [fmtTouched, setFmtTouched] = useState(false);
  const [q, setQ] = useState("");
  // M52：技能包「要给什么」+ 包内自带样例。切技能就拉一次，切回通用助手清空。
  const [pkg, setPkg] = useState<{
    checklist: string[]; samples: { filename: string; display_name?: string; kind: string; size: number }[];
    can_run_with_samples: boolean; input_sample_count?: number; hint: string;
  } | null>(null);
  const [showChecklist, setShowChecklist] = useState(false);
  const [useSamples, setUseSamples] = useState(false);

  const boxRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  // P0-1：当前流式请求的句柄，用来点「停止」时取消（abort + DELETE /agent/stream/{id}）
  const streamRef = useRef<{ done: Promise<void>; cancel: () => Promise<void> } | null>(null);
  // P1-5：每一代流式请求递增一个 id；旧请求（被 steer 取消的）的回调全部以 genId 失配早退，
  // 避免旧流 finally 把 busy 复位 / 旧流的 token 回填覆盖新一轮答案。
  const genRef = useRef(0);

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

  // 技能一换就把它的输入清单/样例取回来。失败静默——这是辅助信息，
  // 取不到不该挡住用户直接提问。
  useEffect(() => {
    setUseSamples(false);
    setShowChecklist(false);
    if (!agentId || (agent as any)?.builtin) { setPkg(null); return; }
    let alive = true;
    api.getSkillInputs(agentId)
      .then((r) => { if (alive) setPkg(r.data); })
      .catch(() => { if (alive) setPkg(null); });
    return () => { alive = false; };
  }, [agentId, (agent as any)?.builtin]);
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
    // 平台优先 HTML：选技能包时默认 HTML，技能自然格式仅作提示标签（Skills 页展示）。
    // 用户手动改格式后不再替他改（fmtTouched）。
    if (!fmtTouched) setFormats(["html"]);
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

  // 刷新会话轮次（回滚后用）：把后端返回的 turns 映射成前端 Turn 结构
  const refreshTurns = useCallback(async (sid: string) => {
    try {
      const r = await api.getSession(sid);
      const list = (r?.data?.turns || []) as any[];
      setTurns(list.map((t: any) => ({
        role: t.role, content: t.content, artifacts: t.artifacts,
        llmReal: t.llmReal, webSources: t.webSources, webSearch: t.webSearch,
      })));
    } catch { /* ignore */ }
  }, []);

  // P1-5 轮次回滚：截断会话到指定轮数（turn 缺省回退一轮），然后刷新界面轮次。
  // 制品版本化不动，旧的 artifact 仍在文件库可单独打开。
  const rollbackTurn = useCallback(async (turn?: number) => {
    if (!sessionId) { setErr("当前没有可回滚的会话"); return; }
    if (busy) { setErr("生成中无法回滚，请先停止或等完成"); return; }
    setErr("");
    try {
      await api.rollbackSession(sessionId, turn);
      await refreshTurns(sessionId);
    } catch (e: any) {
      setErr(e?.message || "回滚失败");
    }
  }, [sessionId, busy, refreshTurns]);

  // P0-1：流式发送核心。先挂 user 轮 + 空 assistant 占位轮（steer 模式则清空最后一条
  // assistant 轮、不新增 user 轮），再逐 token 增量填充；点「停止」走 streamRef.cancel()
  // （abort + DELETE），后端 emit cancelled 后流自然结束。
  // opts.steer=true：把「上一轮用户问题 + 补充约束」合并重生成（取消本会话旧流后执行）。
  const runStream = (opts: {
    input: string; fileIds?: string[]; fileNames?: string[]; sampleRun?: boolean;
    steer?: boolean; steerInput?: string;
  }) => {
    if (!agentId) return;
    const genId = ++genRef.current;            // 本代 id；旧代回调凭失配早退
    setBusy(true); setErr("");
    if (opts.steer) {
      // 不新增 user 轮：追加一条新的空 assistant 轮（后端会重生成答案并 append 到此轮），
      // 旧的（被取消的）助手回答保留在上一条，形成「旧答案→补充要求→新答案」的对照。
      setTurns((t) => [...t, { role: "assistant", content: "" }]);
    } else {
      const names = opts.fileNames || [];
      setTurns((t) => [...t,
        { role: "user", content: opts.input, files: names },
        { role: "assistant", content: "" },
      ]);
    }
    setInput("");
    if (!opts.steer) setAttachments([]);

    const acc = { answer: "", done: null as any, finalized: null as any, steps: [] as ToolStep[],
                  cancelled: false, cancelledSessionId: null as string | null, errored: "" };
    const flush = (extra: Partial<Turn>) => {
      if (genId !== genRef.current) return;     // 已被新一代取代，不再写
      setTurns((t) => {
        const copy = t.slice();
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") copy[copy.length - 1] = { ...last, ...extra };
        return copy;
      });
    };
    const flushSteps = () => flush({ toolSteps: acc.steps });

    const stream = api.streamAgent(agentId, opts.input, sessionId || undefined,
      opts.fileIds, formats, !!opts.sampleRun, (ev) => {
        if (genId !== genRef.current) return;   // 旧流残余事件，丢弃
        if (ev.type === "token") {
          acc.answer += ev.text;
          flush({ content: acc.answer });
        } else if (ev.type === "tool_call") {
          acc.steps = [...acc.steps, { tool: ev.tool, args: ev.args }];
          flushSteps();
        } else if (ev.type === "tool_result") {
          const idx = [...acc.steps].reverse().findIndex((s) => s.tool === ev.tool && s.ok === undefined);
          if (idx >= 0) {
            const realIdx = acc.steps.length - 1 - idx;
            acc.steps = acc.steps.map((s, i) =>
              i === realIdx ? { ...s, ok: ev.ok, content: ev.content } : s);
          } else {
            acc.steps = [...acc.steps, { tool: ev.tool, ok: ev.ok, content: ev.content }];
          }
          flushSteps();
        } else if (ev.type === "tool_escalated") {
          acc.steps = [...acc.steps, { tool: ev.tool, args: ev.args, escalated: true, ticketId: ev.ticketId }];
          flushSteps();
        } else if (ev.type === "done") {
          acc.done = ev.result;
          const r = ev.result || {};
          flush({
            content: r.answer ?? acc.answer,
            skillApplied: r.agent?.skill_applied,
            llmReal: r.usage?.real,
            llmError: r.usage?.error,
            webSources: r.web_sources || [],
            webSearch: r.web_search,
            samplesUsed: (r.samples_used || []).map((s: any) => s.filename),
          });
        } else if (ev.type === "finalized") {
          acc.finalized = ev;
          flush({ runId: ev.run_id, artifacts: ev.artifacts || [] });
        } else if (ev.type === "cancelled") {
          acc.cancelled = true;
          acc.cancelledSessionId = ev.session_id || null;
        } else if (ev.type === "error") {
          acc.errored = ev.detail || "生成失败";
        }
      }, !!opts.steer, opts.steerInput);
    streamRef.current = stream;

    stream.done
      .then(() => {
        if (genId !== genRef.current) return;   // 已被新一代取代
        const f = acc.finalized || {};
        const sid = (acc.done?.session?.session_id) || f.session_id || acc.cancelledSessionId;
        if (sid) setSessionId(sid);
        // steer 复用已有会话，不覆盖原标题；普通发送才用首句命名
        onSession(sid, opts.steer ? undefined : (opts.input || "").slice(0, 26));
        setAttachments([]);
        const arts = (f.artifacts || []) as any[];
        if (arts.length) openPreview(arts[0]);
        if (acc.cancelled) setErr("已取消生成（已保留已输出的内容）");
        else if (acc.errored) setErr(acc.errored);
      })
      .catch((e: any) => {
        if (genId !== genRef.current) return;
        const msg = e?.message || "执行失败，请重试";
        if (msg.includes("session_busy")) {
          // P1-5：会话正忙。提示等待完成，或输入补充要求重新生成（不要并发打穿）
          setErr("该会话正在生成中，请等待完成；或输入补充要求后发送以重新生成");
        } else if (!acc.cancelled) {
          setErr(msg);
        }
      })
      .finally(() => {
        if (genId !== genRef.current) return;   // 旧代不复位（新一代仍在进行）
        setBusy(false); streamRef.current = null;
      });
  };

  const send = () => {
    const text = input.trim();
    const sampleRun = useSamples && !!pkg?.can_run_with_samples;
    if ((!text && !attachments.length && !sampleRun) || !agentId) return;
    const fileIds = attachments.map((a) => a.file_id);
    const fileNames = attachments.map((a) => a.filename);
    const userContent = text || "请按技能包处理附件";
    runStream({ input: userContent, fileIds, fileNames, sampleRun });
  };

  // P1-5 中途补充要求：取消本会话旧流（后端 ACTIVE_STREAMS 置 cancelled），再以
  // 「上一轮问题 + 补充约束」重生成。文本即约束，不另起 user 轮。
  const steerSend = () => {
    const text = input.trim();
    if (!text) return;
    runStream({ input: text, steer: true, steerInput: text });
  };

  // 发送按钮统一入口：生成中且输入非空 → 补充要求重生成；生成中无输入 → 停止；
  // 非生成中 → 普通发送。
  const onSendButton = () => {
    if (busy) { if (input.trim()) steerSend(); else stopStream(); }
    else send();
  };

  // 取消：abort fetch + 通知后端停生成；后端 emit cancelled 后流自然结束，finally 清 busy
  const stopStream = () => { streamRef.current?.cancel(); };

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
      <div className="chat-main">
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
                    <div className="body">
                      {busy && i === turns.length - 1 && !t.content && !t.toolSteps?.length ? (
                        <span className="pending">
                          <span className="dot" /><span className="dot" /><span className="dot" />
                          正在按技能包流程执行…
                        </span>
                      ) : stripToolBlocks(cleanAnswer(t.content))}
                    </div>
                    {!!t.toolSteps?.length && (
                      <div className="tool-steps">
                        <div className="ts-t">本次流式过程中调用了 {t.toolSteps.length} 个工具</div>
                        {t.toolSteps.map((s, si) => (
                          <div key={si} className={"ts" + (s.escalated ? " esc"
                            : s.ok === false ? " bad" : s.ok ? " ok" : "")}>
                            <b>🔧 {s.tool}</b>
                            {s.escalated ? (
                              <span className="ts-res"> → 高危操作已转人工审批（单 {s.ticketId}），通过后自动执行并回灌本会话</span>
                            ) : (
                              <>
                                {s.args !== undefined && (
                                  <span className="ts-args">{summarizeArgs(s.args)}</span>
                                )}
                                {s.ok !== undefined && (
                                  <span className="ts-res">{s.ok ? " · 执行成功" : " · 执行失败"}：{s.content || ""}</span>
                                )}
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {!!t.artifacts?.length && (
                      <div className="artifacts">
                        {t.artifacts.map((a: any) => {
                          const ext = extOf(a.filename);
                          const onPrev = preview?.a.file_id === a.file_id;
                          const key = a.artifact_key;
                          const vOpen = versOpen === key;
                          const vData = key ? versData[key] : undefined;
                          const latestVer = vData?.versions?.[0]?.version ?? a.version;
                          return (
                            <div key={a.file_id} style={{ display: "flex", flexDirection: "column", width: 212 }}>
                              <div className={"artifact" + (onPrev ? " on" : "")}
                                   onClick={() => openPreview(a)}
                                   title={`${a.filename} · ${kb(a.size)} · 点击右侧预览`}>
                                <div className={"ext " + ext}>{dlBusy === a.file_id ? "…" : ext.toUpperCase()}</div>
                                <div className="meta">
                                  <b>{niceName(a.filename)}</b>
                                  <span>{dlBusy === a.file_id ? "准备中…"
                                    : `${ext.toUpperCase()} · ${kb(a.size)} · 点击预览`}</span>
                                </div>
                                {a.version > 0 && (
                                  <span className="vbadge" onClick={(e) => { e.stopPropagation(); toggleVersions(a); }}
                                        title="查看历史版本 / 回滚">v{a.version} ↺</span>
                                )}
                              </div>
                              {vOpen && vData && (
                                <div className="vpanel" onClick={(e) => e.stopPropagation()}>
                                  <div className="vp-t">历史版本 · 共 {vData.count} 个（最新 v{latestVer}）</div>
                                  {vData.versions.map((v: any) => (
                                    <div key={v.file_id} className="vp-row">
                                      <span className="vp-v">v{v.version}</span>
                                      <span className="vp-name" title={v.filename}>{niceName(v.filename)}</span>
                                      <span className="vp-size">{kb(v.size)}</span>
                                      {v.meta?.rollback && <span className="vp-roll">回滚</span>}
                                      <button className="vp-dl" onClick={() => openPreview({ ...a, ...v })}
                                              title="预览该版本">预览</button>
                                      <button className="vp-rb"
                                              disabled={v.version === latestVer || versBusy === `${key}:${v.version}`}
                                              onClick={() => rollbackVersion(a, v.version)}
                                              title={v.version === latestVer ? "已是最新版本" : "把此版本复制为最新（旧版本保留）"}>
                                        {versBusy === `${key}:${v.version}` ? "…" : "回滚到此"}
                                      </button>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {!!t.webSources?.length && (
                      <div style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 7 }}>
                        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                          联网来源 · {t.webSources.length} 条（公开网页，非公司内部资料）
                        </div>
                        {t.webSources.slice(0, 4).map((s, i) => (
                          <a key={`${s.url}-${i}`} href={s.url} target="_blank" rel="noopener noreferrer"
                             style={{ display: "block", fontSize: 11.5, lineHeight: 1.7,
                                      color: "var(--blue)", textDecoration: "none",
                                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            [W{i + 1}] {s.title || s.url}
                          </a>
                        ))}
                        {t.webSources.length > 4 && (
                          <div style={{ fontSize: 11, color: "var(--muted)" }}>
                            其余 {t.webSources.length - 4} 条见产出物「来源」页
                          </div>
                        )}
                      </div>
                    )}
                    {t.webSearch?.degraded && (
                      <div className="notice">联网通道已降级（{t.webSearch.mode || "unavailable"}），
                        本次结果请人工复核来源。</div>
                    )}
                    {t.llmReal === false && (
                      <div className="notice">
                        本次未走真模型{t.llmError ? `（${t.llmError}）` : ""}，内容仅供参考，不要对外使用。
                      </div>
                    )}
                    {!!t.samplesUsed?.length && (
                      <div className="sample-note">
                        本次使用了技能包自带样例：{t.samplesUsed.join("、")}（演示数据，替换成自己的文件再跑一次）
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
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

          {/* M52：技能包作者的输入清单 + 包里自带的原始输入。
              用户之前只能看到一句自由描述，于是反复被模型反问「请提供输入」。 */}
          {agent && !agent.builtin && !!pkg && (
            (pkg.checklist.length > 0 || pkg.can_run_with_samples) && (
              <div className="p-inputs">
                {pkg.checklist.length > 0 && <span className="pi-t">要给：</span>}
                {pkg.checklist.slice(0, showChecklist ? 99 : 3).map((c, i) => (
                  <span key={`${c}-${i}`} className="pi-i" title={c}>{c}</span>
                ))}
                {pkg.checklist.length > 3 && (
                  <button className="pi-more" onClick={() => setShowChecklist((v) => !v)}>
                    {showChecklist ? "收起" : `还有 ${pkg.checklist.length - 3} 项`}
                  </button>
                )}
                {pkg.can_run_with_samples ? (
                  <button className={"pi-sample" + (useSamples ? " on" : "")}
                          onClick={() => setUseSamples((v) => !v)}
                          title={`包里带 ${pkg.input_sample_count ?? pkg.samples.filter((s) => s.kind === "input").length} 份原始输入，勾上就不用自己准备数据：\n`
                            + pkg.samples.filter((s) => s.kind === "input")
                                .map((s) => `· ${s.display_name || s.filename}`).join("\n")}>
                    {useSamples ? "✓ 用样例数据" : "用样例数据试跑"}
                  </button>
                ) : (
                  <span className="pi-hint">这个包没带样例，需自己上传文件</span>
                )}
              </div>
            )
          )}

          <textarea
            ref={taRef}
            rows={1}
            value={input}
            placeholder={busy
              ? "生成中——输入补充要求后发送将重新生成（不新增问题轮）；留空发送则停止"
              : (agent && !agent.builtin
                ? (useSamples && pkg?.can_run_with_samples
                    ? "样例数据已就位，说明要处理什么即可；留空直接回车也行"
                    : pkg?.can_run_with_samples
                      ? `说明这次要处理什么；不想准备数据可勾上方「用样例数据试跑」`
                      : `说明这次要处理什么，${agent.name}会按流程执行`)
                : empty ? "描述你要做的事，或先上传文件…" : "继续追问，或上传新文件")}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSendButton(); }
            }}
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

            <button className="tool" onClick={() => rollbackTurn()}
                    disabled={busy || !sessionId || turns.length === 0}
                    title="回滚一轮：删掉最后一轮（通常是不满意的助手回答），再重新发问；制品旧版本仍在文件库">
              ↩ 回滚一轮
            </button>

            <button className="send" onClick={onSendButton}
                    disabled={!busy && (!agentId || (!input.trim() && !attachments.length
                              && !(useSamples && pkg?.can_run_with_samples)))}
                    title={busy ? "生成中：输入补充要求发送=重新生成；留空发送=停止" : "发送（Enter）"}>
              {busy ? "■" : "↑"}
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
          技能包会按自己的流程执行，产出物在右侧看板直接预览，也可下载。对话仅你本人可见。
        </div>
      </div>
      </div>

      {/* 右侧看板：产出物内联预览，不用先下载 */}
      {preview && (
        <div className="preview-pane">
          <div className="preview-head">
            <b>产出预览</b>
            <span className="p-ext">{preview.ext.toUpperCase()}</span>
            <span className="grow" />
            <button className="p-dl" onClick={() => download(preview.a)} title="下载原文件">下载</button>
            <span className="p-x" onClick={closePreview} title="关闭预览">×</span>
          </div>
          <div className="preview-body">
            {["html", "pdf"].includes(preview.ext) ? (
              <iframe className="preview-frame" src={preview.url} title={preview.a.filename} />
            ) : (
              <div className="preview-fallback">
                <div className={"pf-ico " + preview.ext}>{preview.ext.toUpperCase()}</div>
                <p>该格式暂不支持内联预览，请下载后查看。</p>
                <button className="btn blue" onClick={() => download(preview.a)}>
                  下载 {preview.ext.toUpperCase()} 文件
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
