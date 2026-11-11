"""M53 技能库扩容（115 → 203，新增 7 个行业）回归测试。

三件事必须锁住，都是"用户说技能调用没效果"的真实成因：
1. **agent_id 必须确定性**：中文名 slug 会被清空，早先回退到 uuid4 随机值，
   每重启一次 203 个包的 id 全变，前端收藏/历史会话/外部调用全断链。
2. **输入清单必须能读表格**：新包把输入写成 markdown 表格，旧实现取首列，
   清单变成 ['序号','01','02']，用户等于看不到「要给什么」。
3. **新行业要进目录、要有岗位归属**：没登记的行业不进岗位节点，包会在
   岗位视图里整块消失（比归错岗更难发现）。
"""
import os

import pytest

from app.routers.agents import _new_agent_id
from app.services import role_map, skill_pack


def _solutions_root():
    from scripts.import_solutions import SOLUTIONS_ROOT
    return SOLUTIONS_ROOT


# ---------------------------------------------------------------- agent_id
def test_agent_id_is_deterministic_for_chinese_names():
    """中文技能包的 id 必须每次算出来都一样，否则重启即断链。"""
    name = "对公客群赛道映射与面访作战卡"
    a, b = _new_agent_id(name), _new_agent_id(name)
    assert a == b
    assert a.startswith("ag_")
    # 不能是纯随机 hex：同一进程内两次相同只是必要条件，再校验不同名的 id 不同
    assert _new_agent_id("数据治理痛点梳理与机制设计") != a


def test_agent_id_keeps_slug_for_ascii_names():
    """英文名仍走可读 slug（不破坏已有数据与外部引用）。"""
    assert _new_agent_id("CRM-sync") == "ag_crmsync"
    assert _new_agent_id("voc-quality-closed-loop").startswith("ag_vocqualityclosed")


def test_agent_id_stable_across_processes():
    """跨进程稳定性：用一个已知中文名固定住算法输出。

    这条用例的意义是「改算法必须是有意识的」——如果哪天有人把 uuid5 换成 uuid4
    或改了命名空间，这里会红，提醒他 203 个包的 id 会全变。
    """
    assert _new_agent_id("图纸询价智能报价") == "ag_ae9742b8"


# ---------------------------------------------------------------- 输入清单
def _checklist_by_name(name: str):
    import glob
    from scripts.import_solutions import scan
    for d in scan():
        if d["name"] == name:
            return skill_pack.checklist({"source_path": d["source"]})
    raise AssertionError(f"没找到包：{name}")


@pytest.mark.parametrize("name,expect", [
    # 表头是「序号 | 文件 | 关键字段」——旧实现会返回 ['序号','01','02']
    ("齐套与缺料预警", "待开工工单.csv"),
    # 表头是「# | 输入文件 | 必需 | 关键字段」
    ("对公客群赛道映射与面访作战卡", "待经营企业名单.csv"),
])
def test_checklist_reads_table_not_first_column(name, expect):
    items = _checklist_by_name(name)
    assert items, f"{name} 的输入清单不该为空"
    assert expect in items, f"{name} 清单应包含 {expect}，实际 {items[:5]}"
    for junk in ("#", "序号", "文件", "输入文件", "01", "02"):
        assert junk not in items, f"{name} 清单混入了表头/序号：{junk}"


def test_no_pack_has_junk_checklist():
    """全量扫一遍：203 个包的清单不能出现纯序号、表头、引用块。"""
    from scripts.import_solutions import scan
    junk_exact = {"#", "序号", "文件", "输入", "输入项", "输入文件", "材料", "项"}
    bad = []
    for d in scan():
        items = skill_pack.checklist({"source_path": d["source"]})
        if not items:
            bad.append((d["name"], "空清单"))
            continue
        for it in items:
            if it in junk_exact or it.isdigit() or it.startswith(">"):
                bad.append((d["name"], it))
                break
    assert not bad, f"清单解析仍有垃圾项：{bad[:8]}"


# ---------------------------------------------------------------- 行业与岗位
def test_all_packs_have_role():
    """每个包都必须有主岗位——空岗位会让包在岗位视图里消失。"""
    from scripts.import_solutions import scan
    empty = []
    for d in scan():
        r = role_map.primary_role(d["name"], d["sub_function"], d["board_cn"])
        if not r:
            empty.append(d["name"])
    assert not empty, f"这些包没有岗位归属：{empty[:10]}"


def test_new_industries_registered():
    """7 个新行业的展示名与一句话说明都要有，否则前端显示原始目录名。"""
    from app.services.agent_store import INDUSTRY_NOTE
    for cn in ("金融业", "教育培训", "医疗健康", "电信业", "能源化工", "汽车产业", "泛互联网"):
        assert INDUSTRY_NOTE.get(cn), f"{cn} 缺一句话说明"


def test_role_map_covers_all_packs():
    """映射表条数必须等于实际包数（漏登记会静默归到兜底岗）。"""
    from scripts.import_solutions import scan
    defs = scan()
    assert len(role_map.PACK_ROLES) == len(defs), (
        f"PACK_ROLES {len(role_map.PACK_ROLES)} 条 vs 实际 {len(defs)} 包")
    assert not role_map.validate()
