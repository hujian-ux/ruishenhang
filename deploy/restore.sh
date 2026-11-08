#!/usr/bin/env bash
# 睿深行 AI 中台 · 生产恢复脚本（对应 backup.sh 的产物）。
#
# 用法（在仓库根目录执行）：
#   ./deploy/restore.sh backups/20261108-120000
#
# 警告：
#   - 恢复会覆盖目标组件当前数据，请先在隔离环境验证，或先对当前态做一次 backup.sh。
#   - PostgreSQL 恢复前需 postgres 容器已起；卷恢复建议先停对应服务再解 tar，避免写冲突。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
SRC="${1:-}"
if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
  echo "用法: ./deploy/restore.sh <备份目录，如 backups/20261108-120000>" >&2
  exit 1
fi
echo "== 从 $SRC 恢复 =="

# ── PostgreSQL 逻辑恢复 ──
if [ -f "$SRC/postgres.sql.gz" ]; then
  if docker compose ps postgres 2>/dev/null | grep -q "Up"; then
    gunzip -c "$SRC/postgres.sql.gz" | docker compose exec -T postgres psql -U app -d aiplat \
      && echo "postgres: 恢复完成" || echo "postgres: 恢复失败"
  else
    echo "postgres: SKIP (容器未运行，先 docker compose up -d postgres)"
  fi
else
  echo "postgres: SKIP (无 postgres.sql.gz)"
fi

# ── 命名卷恢复 ──
for vol in qdrant_storage minio_data redis_data pgdata caddy_data backend_data; do
  f="$SRC/${vol}.tar.gz"
  full="$(docker volume ls -q | grep -E "(^|/)${vol}$" | head -1)"
  if [ -f "$f" ] && [ -n "$full" ]; then
    docker run --rm -v "${full}:/dest" -v "$PWD/$SRC:/src" alpine sh -c "cd /dest && tar xzf /src/${vol}.tar.gz" 2>/dev/null \
      && echo "$vol: 恢复完成" || echo "$vol: SKIP (tar 解包失败)"
  else
    echo "$vol: SKIP (无 ${vol}.tar.gz 或卷不存在)"
  fi
done

echo "== 恢复结束（卷恢复后建议重启对应服务：docker compose restart <svc>）=="
