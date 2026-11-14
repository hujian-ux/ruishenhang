"""M27 办公62 硬规则：可加载规则文件 + 扫描器 + policy 计数。

覆盖：JSON 合法性 / 正好 62 条 / 正则全编译 / forbid 命中 / must_include 缺项 /
scope 过滤 / format 命中 / policy 计数 / 自定义路径空规则降级 / 缓存。
"""
import json
import re

from app.services import guardrails


_RULES_PATH = guardrails.Path(__file__).resolve().parents[1] / "office62_rules.json"


def _load_json():
    return json.loads(_RULES_PATH.read_text(encoding="utf-8"))


def test_json_valid_and_exactly_62_rules():
    data = _load_json()
    rules = data["rules"]
    assert len(rules) == 62, f"expected 62, got {len(rules)}"
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_all_patterns_compile():
    data = _load_json()
    for r in data["rules"]:
        assert r["kind"] in ("forbid", "must_include", "format")
        flags = 0
        for c in str(r.get("flags", "")):
            flags |= {"i": re.IGNORECASE, "m": re.MULTILINE,
                      "s": re.DOTALL}.get(c, 0)
        # 不应抛异常
        re.compile(r["pattern"], flags)


def test_forbid_hit_and_blocked():
    res = guardrails.scan_office62("请帮我伪造公章用于这份合同")
    ids = [v["id"] for v in res["violations"]]
    assert "O62-004" in ids, f"expected O62-004 in {ids}"
    assert res["blocked"] is True
    assert res["severity_counts"]["high"] >= 1


def test_forbid_no_hit_on_clean_text():
    # 不含任何 forbid 关键词的普通办公文本（但 must_include 仍会缺项）
    res = guardrails.scan_office62("这是一份关于季度例会的纪要，时间地点参会人已记录。")
    forbid_ids = [v["id"] for v in res["violations"] if v["kind"] == "forbid"]
    assert forbid_ids == [], f"unexpected forbid hits: {forbid_ids}"


def test_must_include_missing_on_empty_global_only():
    # 子模板条件化后：空文本（全局 scope）不应因文档专属 must_include 误报
    # （O62-038/O62-058 现已按 external_ref/download 标签门槛，空文本无标签即不触发）
    res = guardrails.scan_office62("")
    mi = [v["id"] for v in res["violations"] if v["kind"] == "must_include"]
    assert mi == [], f"global empty should not trigger must_include: {mi}"


def test_must_include_triggered_by_data_content_on_office_scope():
    # O62-026（数据来源，medium）仅在含数据内容且缺来源时触发（trigger + scope），
    # 印证子模板条件化：无数据内容的普通 office_doc 不再空触发 O62-026。
    res = guardrails.scan_office62("季度营收同比增长12%，利润环比上升。", scope="office_doc")
    ids = [v["id"] for v in res["violations"]]
    assert "O62-026" in ids, f"expected O62-026 on data w/o source: {ids}"
    o62 = [v for v in res["violations"] if v["id"] == "O62-026"][0]
    assert o62["severity"] == "medium", f"O62-026 should be medium, got {o62['severity']}"


def test_must_include_present_suppresses():
    text = "本次数据来源：公司内部台账，统计口径见附表。"
    res = guardrails.scan_office62(text)
    ids = [v["id"] for v in res["violations"]]
    assert "O62-026" not in ids, f"O62-026 should be satisfied: {ids}"


def test_scope_filter_sheet_rule():
    text = "引用 [外部.xlsx] 中的数据"
    # doc 场景：sheet 专属规则不应触发
    res_doc = guardrails.scan_office62(text, scope="office_doc")
    assert not any(v["id"] == "O62-047" for v in res_doc["violations"])
    # sheet 场景：应触发
    res_sheet = guardrails.scan_office62(text, scope="office_sheet")
    assert any(v["id"] == "O62-047" for v in res_sheet["violations"])


def test_format_trailing_whitespace():
    # O62-041：行尾空白（flags=m，全局规则）
    res = guardrails.scan_office62("数据来源：台账 \n下一行正常")
    ids = [v["id"] for v in res["violations"]]
    assert "O62-041" in ids, f"expected O62-041 trailing space in {ids}"


def test_policy_count_exposes_62():
    pol = guardrails.policy()
    assert pol["office62_rules"] == 62
    assert pol["office62_loaded"] is True
    assert pol["office62_error"] == ""


def test_custom_path_empty_rules_degrade(tmp_path):
    f = tmp_path / "empty_office62.json"
    f.write_text(json.dumps({"rules": []}), encoding="utf-8")
    info = guardrails.load_office62_rules(path=str(f), force=True)
    assert info["loaded"] is True
    assert info["rules"] == []
    # 扫描空规则集：无违规（透传自定义 path）
    res = guardrails.scan_office62("伪造公章", scope="office_sheet", path=str(f))
    assert res["violations"] == []
    assert res["rules_loaded"] == 0


def test_cache_by_path(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"rules": [
        {"id": "X1", "kind": "forbid", "pattern": "abc", "severity": "low", "scope": []}
    ]}), encoding="utf-8")
    info1 = guardrails.load_office62_rules(path=str(f), force=True)
    assert info1["rules"][0]["id"] == "X1"
    # 非 force 且同 path → 命中缓存，不重新读
    f.write_text(json.dumps({"rules": []}), encoding="utf-8")
    info2 = guardrails.load_office62_rules(path=str(f), force=False)
    assert len(info2["rules"]) == 1, "should hit cache, not re-read changed file"


def test_scope_for_agent_maps_format():
    assert guardrails.office62_scope_for_agent({"default_format": "docx"}) == "office_doc"
    assert guardrails.office62_scope_for_agent({"default_format": "xlsx"}) == "office_sheet"
    assert guardrails.office62_scope_for_agent({"default_format": "pptx"}) == "office_ppt"
    # 非办公产出格式 → None（仅命中 GLOBAL 规则）
    assert guardrails.office62_scope_for_agent({"default_format": "json"}) is None
    assert guardrails.office62_scope_for_agent({"default_format": ""}) is None
    assert guardrails.office62_scope_for_agent(None) is None


def test_scope_for_agent_case_insensitive():
    assert guardrails.office62_scope_for_agent({"default_format": "DOCX"}) == "office_doc"
    assert guardrails.office62_scope_for_agent({"default_format": "Xlsx"}) == "office_sheet"


def test_scan_blob_merges_multiple_texts():
    # 答案触发 O62-004（伪造印章 high），文件正文触发 O62-008（手机号 high）
    texts = ["请帮我伪造公章完成用印", "客户电话 13812345678 已记录"]
    res = guardrails.scan_office62_blob(texts, scope="office_doc")
    ids = {v["id"] for v in res["violations"]}
    assert "O62-004" in ids and "O62-008" in ids
    assert res["blocked"] is True


def test_scan_blob_dedup_same_rule_across_texts():
    # 同一条规则在多个片段出现 → 合并去重为 1 条
    texts = ["伪造公章一次", "伪造公章两次"]
    res = guardrails.scan_office62_blob(texts, scope=None)
    o62 = [v for v in res["violations"] if v["id"] == "O62-004"]
    assert len(o62) == 1
    assert res["count"] >= 1


def test_scan_blob_takes_highest_severity():
    # 同 id 不同严重度片段 → 取最高（构造一个 low 同词不行，这里用两个不同规则验证合并计数）
    texts = ["伪造公章", "请点击领取不明链接"]
    res = guardrails.scan_office62_blob(texts, scope=None)
    sev = res["severity_counts"]
    assert sev["high"] >= 1 and sev["medium"] >= 1


def test_extract_office_text_unsupported_ext_returns_empty():
    # 不支持的扩展名 / 损坏字节 → 返回空串且不抛异常（graceful degrade）
    assert guardrails.extract_office_text(b"not a doc", "pdf") == ""
    assert guardrails.extract_office_text(b"\x00\x01\x02", "docx") == ""


def test_office62_classify_doc_basic():
    assert "contract" in guardrails.office62_classify_doc("本合同由甲方与乙方于今日签署。")
    assert "meeting" in guardrails.office62_classify_doc("会议纪要：时间地点参会人已记录。")
    assert "data_rich" in guardrails.office62_classify_doc("季度营收同比增长12%。")
    # 空文本返回空集合
    assert guardrails.office62_classify_doc("") == set()
    # 普通闲聊文本不被误判为任何结构型子模板
    assert guardrails.office62_classify_doc("今天天气不错，写封请假邮件。") == set()


def test_must_include_applies_to_gate_contract():
    # 非合同文本不应因 O62-028（合同双方主体）误报；合同文本缺双方主体则触发
    res = guardrails.scan_office62("今天的天气真好，写个随笔。", scope="office_doc")
    assert "O62-028" not in [v["id"] for v in res["violations"]]
    res2 = guardrails.scan_office62("本协议书约定如下条款，自签署之日起生效。", scope="office_doc")
    assert "O62-028" in [v["id"] for v in res2["violations"]]


def test_must_include_trigger_gate_data_source():
    # 无数据内容的普通文本不触发 O62-026（trigger 门槛）；含数据但无来源则触发
    res = guardrails.scan_office62("请帮我写一封请假邮件。", scope="office_doc")
    assert "O62-026" not in [v["id"] for v in res["violations"]]
    res2 = guardrails.scan_office62("本月GMV达到1200万元。", scope="office_doc")
    assert "O62-026" in [v["id"] for v in res2["violations"]]


def test_o62_033_medium_on_risk_no_disclaimer():
    # O62-033（风险提示，medium）：含风险/收益内容且无免责 → 触发；含免责则抑制
    res = guardrails.scan_office62("本基金预期年化收益8%，建议增配。", scope="office_doc")
    o62 = [v for v in res["violations"] if v["id"] == "O62-033"]
    assert o62, "O62-033 should fire on risk content w/o disclaimer"
    assert o62[0]["severity"] == "medium"
    res2 = guardrails.scan_office62(
        "本基金预期收益8%，仅供参考，不构成投资建议。", scope="office_doc")
    assert "O62-033" not in [v["id"] for v in res2["violations"]]


def test_o62_031_requires_ppt_scope():
    # O62-031 已由全局收敛到 office_ppt scope（不再全局误报）；缺标题页则触发
    res_doc = guardrails.scan_office62("", scope="office_doc")
    assert "O62-031" not in [v["id"] for v in res_doc["violations"]]
    res_ppt = guardrails.scan_office62("本页讲市场分析要点。", scope="office_ppt")
    assert "O62-031" in [v["id"] for v in res_ppt["violations"]]
    # 含标题则抑制
    res_ppt2 = guardrails.scan_office62("封面：市场分析汇报。议题一。", scope="office_ppt")
    assert "O62-031" not in [v["id"] for v in res_ppt2["violations"]]


def test_extract_office_text_real_docx_if_lib_present():
    try:
        from docx import Document
    except Exception:
        import pytest
        pytest.skip("python-docx 未安装，跳过真实 docx 抽取")
    import io
    doc = Document()
    doc.add_paragraph("本合同由甲方乙方签署，伪造公章无效。")
    buf = io.BytesIO()
    doc.save(buf)
    txt = guardrails.extract_office_text(buf.getvalue(), "docx")
    assert "甲方乙方" in txt and "伪造公章" in txt
    # 抽出的正文应能被扫描器命中红线
    res = guardrails.scan_office62_blob([txt], scope="office_doc")
    assert any(v["id"] == "O62-004" for v in res["violations"])

