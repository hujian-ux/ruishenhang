import { useState } from "react";
import { api, recommendWithBackend } from "../api/client";

// 选型向导（与后端 selection/framework.py 同源）：输入类型 → 确定性 → 核心需求
const STEPS = [
  { key: "inputType", label: "输入类型", options: [["text", "文本"], ["image", "图像"], ["table", "表格"], ["structured", "API"]] },
  { key: "deterministic", label: "确定性/可解释", options: [[true, "是"], [false, "否"]] },
  { key: "need", label: "核心需求", options: [["knowledge", "企业知识"], ["creative", "开放生成"], ["accuracy", "精度优先"], ["explain", "强解释"]] },
];

export function AgentsPage() {
  const [ans, setAns] = useState<Record<string, any>>({});
  const [result, setResult] = useState<any>(null);
  const [form, setForm] = useState({ name: "", description: "", knowledge_scope: "department", model_id: "" });
  const [created, setCreated] = useState<string>("");

  const stepIdx = STEPS.findIndex((s) => !(s.key in ans));
  const done = stepIdx === -1;

  const choose = async (key: string, val: any) => {
    const next = { ...ans, [key]: val };
    setAns(next);
    if (Object.keys(next).length === STEPS.length) {
      // 联调后端选型服务（/selection/recommend）；失败回退本地规则
      const res = await recommendWithBackend(next.inputType, next.deterministic, next.need);
      setResult(res);
    }
  };

  const create = async () => {
    if (!result) return;
    try {
      const r = await api.registerAgent({
        name: form.name, adapter_type: result.adapter_type,
        description: form.description, knowledge_scope: form.knowledge_scope,
        model_id: form.model_id || undefined,
        selection_rationale: { recommended: result.adapter_type, landing_chapters: result.landing_chapters || "" },
      });
      setCreated(`已创建，agent_id=${(r.data as any).agent_id}，状态=${(r.data as any).status}（第35章：待发布评审）`);
    } catch (e: any) { setCreated("失败：" + e.message); }
  };

  return (
    <>
      <div className="card">
        <h3>① 模型选型向导（第35章）</h3>
        {STEPS.map((s, i) => (
          <div className="field" key={s.key}>
            <label>{i + 1}. {s.label}</label>
            <div className="row">
              {s.options.map(([v, label]) => (
                <div key={String(v)} className={"seg " + (ans[s.key] === v ? "on" : "")}
                  onClick={() => choose(s.key, v)}>{label}</div>
              ))}
            </div>
          </div>
        ))}
        {!done && <div className="hint">请完成上面 {stepIdx + 1} 步…</div>}
        {result && (
          <div className="ok">
            <span className="tag">{result.tag}</span> 推荐：<b>{result.name}</b>（adapter_type={result.adapter_type}）
            {result.landing_chapters && (
              <div className="hint" style={{ marginTop: 6 }}>落地章节（后端选型服务）：{result.landing_chapters}</div>
            )}
            {result.why && (
              <div className="hint" style={{ marginTop: 4 }}>理由：{result.why}</div>
            )}
            {!result.landing_chapters && (
              <div className="hint" style={{ marginTop: 6 }}>（后端选型服务不可达，已回退本地规则）</div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h3>② 新建智能体（选型结论自动带入）</h3>
        <div className="field"><label>名称</label><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div className="field"><label>选型说明</label>
          <textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder={result ? `选型=${result.adapter_type}（${result.name}）` : "先完成选型向导"} />
        </div>
        <div className="field"><label>知识库范围（多人知识库）</label>
          <select value={form.knowledge_scope} onChange={(e) => setForm({ ...form, knowledge_scope: e.target.value })}>
            <option value="personal">personal 本人</option>
            <option value="owner">owner 负责人</option>
            <option value="department">department 部门</option>
            <option value="global">global 集团</option>
          </select>
        </div>
        <button className="btn" disabled={!result || !form.name} onClick={create}>注册智能体</button>
        {created && <div className="ok">{created}</div>}
      </div>
    </>
  );
}
