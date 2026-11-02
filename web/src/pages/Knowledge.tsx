import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const SCOPES = [
  { k: "personal", label: "个人" },
  { k: "department", label: "本部门" },
  { k: "global", label: "集团（仅管理员）" },
];

export function KnowledgePage() {
  const [docs, setDocs] = useState<any[]>([]);
  const [storeOk, setStoreOk] = useState(false);
  const [scope, setScope] = useState("department");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const r = await api.listKbDocuments();
      setDocs(r.data.documents);
      setStoreOk(r.data.object_store_available);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const doUpload = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) { setErr("先选一个文件（txt / md / csv / json）"); return; }
    setBusy(true); setErr(""); setMsg("");
    try {
      const r = await api.uploadKbDocument(f, title || f.name.replace(/\.[^.]+$/, ""), scope);
      setMsg(`已入库 ${r.data.doc_id}（${r.data.chars} 字）· 已入检索语料，可直接被对话召回 · 存储 ${r.data.storage}`);
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
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
      setMsg(`已删除 ${id}：注册表 / 检索语料 / 对象存储三处同步清理`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <>
      <div className="card">
        <h3>知识库上传（第5/18/23章 · 上传即可被召回）</h3>
        <div className="hint" style={{ marginBottom: 10 }}>
          对象存储：{storeOk ? "已连接（MinIO / S3 兼容）" : "不可达，原文落内存兜底（接口不变）"} · 支持 txt / md / csv / json / sql，单文件 ≤ 512KB
        </div>
        <div className="row" style={{ gap: 10, alignItems: "flex-end" }}>
          <div className="field" style={{ flex: "1 1 240px", margin: 0 }}>
            <label>选择文件</label>
            <input type="file" ref={fileRef} accept=".txt,.md,.markdown,.csv,.json,.log,.yml,.yaml,.sql" />
          </div>
          <div className="field" style={{ flex: "1 1 180px", margin: 0 }}>
            <label>文档标题（留空用文件名）</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：2026 渠道政策" />
          </div>
          <button className="btn" onClick={doUpload} disabled={busy}>{busy ? "上传中…" : "上传并入库"}</button>
        </div>
        <div className="row" style={{ gap: 6, marginTop: 10 }}>
          {SCOPES.map((s) => (
            <div key={s.k} className={"seg " + (scope === s.k ? "on" : "")} style={{ flex: "0 0 128px" }} onClick={() => setScope(s.k)}>
              {s.label}
            </div>
          ))}
          <span className="hint" style={{ alignSelf: "center" }}>越权范围（跨部门 / 员工写集团）会被服务端 403 拦截</span>
        </div>
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828", marginTop: 8 }}>{err}</div>}
      </div>

      <div className="card">
        <h3>我的知识库（{docs.length} 篇 · 按第18章可见性过滤）</h3>
        {docs.length === 0 && <div className="hint">暂无文档，上面传一个试试。</div>}
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
                  <td>{d.source === "upload" ? d.filename || "上传" : "手工"}</td>
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
