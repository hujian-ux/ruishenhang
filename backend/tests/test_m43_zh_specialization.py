"""M43 · 中文特化：长度感知切分 + 中文提示词模板 + 内部中文基准集。"""
from app.services import zh_chunk, zh_prompts, zh_bench


# ── 长度感知切分 ────────────────────────────────────────────────────────────
def test_merge_short_sentences():
    text = "员工入职需签劳动合同。试用期最长六个月。转正后缴纳社保。离职需提前三十天通知。"
    chunks = zh_chunk.chunk_text(text, max_len=30, min_len=8)
    # 碎句应被合并，块数明显少于句数（7 句 → 更少块）
    assert len(chunks) < 7
    # 每块不超过 max_len
    assert all(zh_chunk._rough_len(c) <= 30 for c in chunks)
    # 合并后总字符守恒（去空白）
    assert sum(zh_chunk._rough_len(c) for c in chunks) == zh_chunk._rough_len(text)


def test_split_overlong_sentence():
    long = "这是一段没有任何标点因此被视为超长句的文本内容需要被强制按长度切分开来以便嵌入和检索"
    chunks = zh_chunk.chunk_text(long, max_len=20, min_len=8)
    assert len(chunks) >= 2
    assert all(zh_chunk._rough_len(c) <= 20 for c in chunks)


def test_empty_input():
    assert zh_chunk.chunk_text("   ") == []
    assert zh_chunk.chunk_text("") == []


# ── 中文提示词模板 ──────────────────────────────────────────────────────────
def test_render_known_task():
    out = zh_prompts.render("summarize", context="资料内容", input="请摘要")
    assert "资料内容" in out and "请摘要" in out
    assert "简体中文" in out


def test_render_unknown_task_raises():
    try:
        zh_prompts.render("nope", input="x")
        assert False, "未知任务应抛 KeyError"
    except KeyError:
        pass


def test_render_missing_var_raises():
    try:
        zh_prompts.render("classify", input="x")  # 缺 labels
        assert False, "缺变量应抛 KeyError"
    except KeyError:
        pass


def test_available_tasks():
    tasks = zh_prompts.available()
    for t in ("summarize", "extract", "classify", "qa", "rewrite", "compliance", "table"):
        assert t in tasks


# ── 内部中文基准集 ──────────────────────────────────────────────────────────
def test_benchmark_keyword_scoring():
    items = zh_bench.list_items()
    # 给正确答案 → 全中
    answers = {it["id"]: "答案：" + "".join(it["expected_keywords"]) for it in items}
    rep = zh_bench.run(items, answers)
    assert rep["hit"] == rep["total"]
    assert rep["pass_rate"] == 1.0


def test_benchmark_partial_and_missing():
    items = zh_bench.list_items()
    # 只答对第一题，其余乱答或缺失
    answers = {items[0]["id"]: items[0]["expected_keywords"][0]}
    rep = zh_bench.run(items, answers)
    assert rep["hit"] == 1
    assert rep["by_category"]  # 按类别聚合存在
    assert rep["source"] == "internal_zh_bench"


def test_benchmark_honest_note():
    rep = zh_bench.run(zh_bench.list_items(), {})
    assert "非官方" in rep["note"] or "C-Eval" in rep["note"]
