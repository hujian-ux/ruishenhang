import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const SCOPES = [
  { k: "personal", label: "个人" },
  { k: "department", label: "本部门" },
  { k: "global", label: "集团（仅管理员）" },
];

const ACCEPT = ".txt,.md,.markdown,.csv,.json,.log,.yml,.yaml,.sql,.html,.htm";
const MAX_BULK = 50;

export function KnowledgePage() {
  const [docs, setDocs] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [storeOk, setStoreOk] = useState(false);
  const [scope, setScope] = useState("department");
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const [r, s] = await Promise.all([api.listKbDocuments(), api.getKbStats()]);
      setDocs(r.data.documents);
      setStoreOk(r.data.object_store_available);
      setStats(s.data);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const onPick = () => {
    const files = Array.from(fileRef.current?.files || []);
    setPicked(files);
    setMsg("");
    setErr("");
    if (files.length > MAX_BULK) setErr(`一次最多 ${MAX_BULK} 个文件（更大批量请用 ingest_corpus CLI）`);
  };

  const resetPicker = () => {
    setPicked([]);
    setTitle("");
    if (fileRef.current) fileRef.current.value = "";
  };

  const doUpload = async () => {
    if (picked.length === 0) { setErr("先选文件（可多选；txt / md / csv / json / html 等文本类）"); return; }
    setBusy(true); setErr(""); setMsg("");
    try {
      if (picked.length === 1 && title.trim()) {
        // 单文件 + 自定义标题 → 走单传（保留人工命名能力）
        const r = await api.uploadKbDocument(picked[0], title.trim(), scope);
        setMsg(`已入库 ${r.data.doc_id}（${r.data.chars} 字）· 已入检索语料，可直接被对话召回 · 存储 ${r.data.storage}`);
      } else {
        // 多文件 → 批量通道：服务端内容去重 + 只重建一次索引
        const r = await api.bulkUploadKbDocuments(picked, scope);
        const d = r.data;
        const dupHint = d.duplicated ? ` · 重复跳过 ${d.duplicated} 篇（内容指纹相同，未重复入库）` : "";
        const failHint = d.skipped ? ` · 失败 ${d.skipped} 篇` : "";
        setMsg(`新增 ${d.added} 篇${dupHint}${failHint} · 新增 ${d.chars_added.toLocaleString()} 字 · 已全部进入同一份检索索引`);
        if (d.failed?.length) {
          setErr(d.failed.map((f: any) => `${f.filename}：${f.reason}`).join("；"));
        }
      }
      resetPicker();
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const view = async (id: string) => {
    setErr("");
    try { setPreview((await api.getKbContent(id)).data); } catch (e: any) { setErr(e.message); }
  };

  const remove = async (id: string) => {
    setErr("");
    try {
      await api.deleteKbDocument(id);
      if (preview?.doc_id === id) setPreview(null);
      setMsg(`已删除 ${id}：注册表 / 检索语料 / 对象存储 / PG 四处同步清理`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const s = stats || {};
  const scopeText = Object.entries(s.by_scope || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—";

  return (
    <>
      <div className="card">
        <h3>语料画像（试点每天开工先看这一屏）</h3>
        <div className="row" style={{ gap: 10 }}>
          <div className="kpi"><span>语料篇数</span><b>{s.documents ?? 0}</b><i>{s.unique_hashes ?? 0} 个内容指纹</i></div>
          <div className="kpi"><span>总字数</span><b>{(s.total_chars ?? 0).toLocaleString()}</b><i>篇均 {s.avg_chars ?? 0} 字</i></div>
          <div className="kpi"><span>范围分布</span><b style={{ fontSize: 14 }}>{scopeText}</b><i>按第18章可见性隔离</i></div>
          <div className="kpi"><span>持久化</span><b style={{ fontSize: 14 }}>{s.pg_available ? "PG 已连接" : "纯内存"}</b><i>{s.pg_available ? "重启不清零" : "重启后需重新导入"}</i></div>
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          对象存储：{s.object_store_available ? "已连接（MinIO / S3 兼容）" : "不可达，原文落内存兜底"}
          {" · "}入索引上限 {s.index_char_cap?.toLocaleString()} 字/篇（超长截断，原文完整保留）
          {s.truncated_documents ? ` · 当前有 ${s.truncated_documents} 篇被截断` : ""}
        </div>
      </div>

      <div className="card">
        <h3>批量上传（第5/18/23章 · 上传即可被召回，同内容自动去重）</h3>
        <div className="row" style={{ gap: 10, alignItems: "flex-end" }}>
          <div className="field" style={{ flex: "1 1 260px", margin: 0 }}>
            <label>选择文件（可多选，单次 ≤ {MAX_BULK} 个）</label>
            <input type="file" ref={fileRef} multiple accept={ACCEPT} onChange={onPick} />
          </div>
          <div className="field" style={{ flex: "1 1 180px", margin: 0 }}>
            <label>文档标题（仅单文件时生效，留空用文件名）</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="如：2026 渠道政策" disabled={picked.length > 1} />
          </div>
          <button className="btn" onClick={doUpload} disabled={busy || picked.length > MAX_BULK}>
            {busy ? "上传中…" : picked.length > 1 ? `批量入库 ${picked.length} 篇` : "上传并入库"}
          </button>
        </div>
        {picked.length > 0 && (
          <div className="hint" style={{ marginTop: 8 }}>
            已选 {picked.length} 个：{picked.slice(0, 6).map((f) => f.name).join("、")}
            {picked.length > 6 ? ` …等 ${picked.length} 个` : ""}
          </div>
        )}
        <div className="row" style={{ gap: 6, marginTop: 10 }}>
          {SCOPES.map((sc) => (
            <div key={sc.k} className={"seg " + (scope === sc.k ? "on" : "")} style={{ flex: "0 0 128px" }} onClick={() => setScope(sc.k)}>
              {sc.label}
            </div>
          ))}
          <span className="hint" style={{ alignSelf: "center" }}>
            越权范围（跨部门 / 员工写集团）会被服务端 403 拦截 · 500 篇以上请用 ingest_corpus 命令行（更快且出报告）
          </span>
        </div>
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828", marginTop: 8 }}>{err}</div>}
      </div>

      <div className="card">
        <h3>我的知识库（{docs.length} 篇 · 按第18章可见性过滤）</h3>
        {docs.length === 0 && <div className="hint">暂无文档，上面传一批试试。</div>}
        {docs.length > 0 && (
          <table className="tbl">
            <thead>
              <tr><th>文档</th><th>范围</th><th>字数</th><th>来源</th><th>可召回</th><th>存储</th><th>操作</th></tr>
            </thead>
            <tbody>
              {docs.map((d: any) => (
                <tr key={d.doc_id}>
                  <td><b>{d.title}</b><div className="hint" style={{ fontSize: 11 }}>{d.doc_id}</div></td>
                  <td><span className="tag">{d.scope}</span></td>
                  <td>{d.chars}</td>
                  <td>{d.source === "manual" ? "手工" : d.filename || d.source}</td>
                  <td>{d.indexed ? "是" : "否"}</td>
                  <td className="hint">{d.storage}</td>
                  <td>
                    <button className="btn" style={{ padding: "4px 9px", fontSize: 11.5 }} onClick={() => view(d.doc_id)}>查看</button>{" "}
                    <button className="btn" style={{ padding: "4px 9px", fontSize: 11.5, background: "#c62828" }} onClick={() => remove(d.doc_id)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!storeOk && <div className="hint" style={{ marginTop: 6 }}>对象存储不可达：原文落在内存，接口行为不变。</div>}
      </div>

      {preview && (
        <div className="card">
          <h3>原文回读 · {preview.title}（{preview.chars} 字，来自 {preview.storage}）</h3>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12.5, lineHeight: 1.65, margin: 0, maxHeight: 320, overflow: "auto" }}>{preview.content}</pre>
        </div>
      )}
    </>
  );
}
