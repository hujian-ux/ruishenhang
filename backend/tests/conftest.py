"""测试环境隔离：默认不连任何外部基础设施，保证可重复。

为什么需要（M9 补）：
  测试会把文档写进 PG `kb_documents` 却不清理——下一次跑时同样的内容会被内容指纹
  判重，`register()` 直接返回 PG 载回的那条记录（`storage="postgres"`），
  于是「新建文档」的断言开始失败。表现是同一份代码第一轮过、第二轮挂，
  这类脏数据比代码 bug 更难查。

做法：
  - 默认把 PG 指向不可达端口（连接立即被拒，不会阻塞），并关闭向量缓存。
    需要真 PG 的验证走 `backend/scripts/validate_m6.py`，不靠单元测试顺带做。
  - 想跑真 PG：`TEST_WITH_PG=1 pytest`。
  - 演示语料保持开启，因为大量断言基于那 18 篇（试点环境的 SEED_DEMO_CORPUS=0 是运行时选择）。
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# 允许测试直接 import scripts/ 下的导入器（import_solutions 等）
_SCRIPTS = str(_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

if os.environ.get("TEST_WITH_PG") != "1":
    # 必须是 import app.core.config 之前：Settings 是类属性，导入即固化
    os.environ["PG_PORT"] = "1"
    os.environ["PG_HOST"] = "127.0.0.1"

os.environ.setdefault("EMBED_CACHE", "0")
os.environ.setdefault("SEED_DEMO_CORPUS", "1")
os.environ.setdefault("USAGE_QUOTA_TOKENS", "2000000")
