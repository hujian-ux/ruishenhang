"""生成「单租户物理隔离栈」的 docker-compose 与 .env 实例。

用法：
  python scripts/gen_tenant_stack.py --tenant acme [--port-offset 0] [--out-dir deploy] [--prod] [--print]

产出（写入 <out-dir>/）：
  docker-compose.<tenant>.yml   独立 PG/Qdrant/MinIO/Redis/backend + 独立网络与卷
  .env.<tenant>                 该租户专属环境变量（含随机库密码、MinIO 密钥）

设计：
  - 模板 deploy/docker-compose.tenant.template.yml 用 ${TENANT} 等文本占位符；本脚本做字符串替换，
    规避 Docker Compose 不支持在 service/volume 键名里做变量插值的限制。
  - 端口：默认复用基准端口（每栈独占一台主机/命名空间）；同一主机多栈用 --port-offset 错开。
  - 每个租户的数据卷与 Docker 网络都带租户前缀，彼此物理隔离、网络不可达。
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = ROOT / "deploy" / "docker-compose.tenant.template.yml"

_BASE_PORTS = {  # 基准端口（无 offset 时）
    "PG_PORT": 5432,
    "QDRANT_PORT": 6333, "QDRANT_PORT4": 6334,
    "MINIO_PORT": 9000, "MINIO_PORT1": 9001,
    "REDIS_PORT": 6379,
    "APP_PORT": 8080,
}


def _slug(tenant: str) -> str:
    s = re.sub(r"[^a-z0-9-]", "-", tenant.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        raise ValueError("租户标识经规范化后为空，请使用字母/数字/连字符")
    return s


def _rand_secret(n: int = 24) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def generate(tenant_raw: str, port_offset: int, prod: bool, out_dir: Path):
    tenant = _slug(tenant_raw)
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"模板不存在：{TEMPLATE}")
    text = TEMPLATE.read_text(encoding="utf-8")

    ports = {k: v + port_offset for k, v in _BASE_PORTS.items()}
    pg_password = _rand_secret()
    minio_user = "minioadmin" if not prod else _rand_secret(12)
    minio_password = _rand_secret()

    repl = {
        "${TENANT}": tenant,
        "${PG_PASSWORD}": pg_password,
        "${MINIO_USER}": minio_user,
        "${MINIO_PASSWORD}": minio_password,
    }
    for k, v in ports.items():
        repl[f"${{{k}}}"] = str(v)

    compose = text
    for ph, val in repl.items():
        compose = compose.replace(ph, val)

    # 残留占位符检查（防止漏替换导致无效 compose）
    left = re.findall(r"\$\{[A-Z_]+}", compose)
    if left:
        raise RuntimeError(f"生成后有未替换占位符：{sorted(set(left))}")

    env_lines = [
        "# 单租户物理隔离栈 · 该租户专属环境变量（由 gen_tenant_stack.py 生成，请补充模型 key）",
        f"APP_ENV={'prod' if prod else 'dev'}",
        f"APP_PORT={ports['APP_PORT']}",
        "API_PREFIX=/api/v1",
        f"PG_HOST={tenant}_postgres",
        "PG_PORT=5432",
        f"PG_DB=aiplat",
        "PG_USER=app",
        f"PG_PASSWORD={pg_password}",
        f"QDRANT_HOST={tenant}_qdrant",
        "QDRANT_PORT=6333",
        f"MINIO_ENDPOINT={tenant}_minio:9000",
        f"MINIO_USER={minio_user}",
        f"MINIO_PASSWORD={minio_password}",
        f"MINIO_BUCKET=kb-files",
        f"REDIS_URL=redis://{tenant}_redis:6379/0",
        "LITELLM_BASE_URL=http://litellm:4000",
        "# ── 以下为可选：模型网关密钥（按客户实际厂商补充）──",
        "OPENAI_API_KEY=",
        "DASHSCOPE_API_KEY=",
        "LITELLM_MASTER_KEY=",
        "# ── 安全（prod 务必改）──",
        "CALLER_SIGNING_SECRET=",
        "WEBHOOK_TOKEN_SECRET=",
        "OIDC_ISSUER=",
        "CORS_ALLOW_ORIGINS=",
        "HTTP_RATE_LIMIT_RPS=",
    ]
    env_text = "\n".join(env_lines) + "\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    compose_path = out_dir / f"docker-compose.{tenant}.yml"
    env_path = out_dir / f".env.{tenant}"
    return tenant, compose, env_text, compose_path, env_path


def main():
    ap = argparse.ArgumentParser(description="生成单租户物理隔离栈")
    ap.add_argument("--tenant", required=True, help="租户标识（字母/数字/连字符）")
    ap.add_argument("--port-offset", type=int, default=0, help="端口偏移（同主机多栈错开）")
    ap.add_argument("--out-dir", default="deploy", help="输出目录（相对项目根或绝对）")
    ap.add_argument("--prod", action="store_true", help="按生产默认值（随机 MinIO 密钥）")
    ap.add_argument("--print", action="store_true", help="仅打印不写文件")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    tenant, compose, env_text, compose_path, env_path = generate(
        args.tenant, args.port_offset, args.prod, out_dir)

    if args.print:
        print(f"# === docker-compose.{tenant}.yml ===")
        print(compose)
        print(f"# === .env.{tenant} ===")
        print(env_text)
        return

    compose_path.write_text(compose, encoding="utf-8")
    # .env 含密钥，权限收紧；Windows 下 chmod 尽力而为
    env_path.write_text(env_text, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    print(f"[OK] 已生成租户栈：{tenant}")
    print(f"   - {compose_path}")
    print(f"   - {env_path}（含随机密钥，已尝试 chmod 600）")
    print(f"   启动：cd {out_dir} && docker compose -f docker-compose.{tenant}.yml --env-file .env.{tenant} up -d")


if __name__ == "__main__":
    sys.exit(main() or 0)
