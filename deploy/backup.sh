#!/usr/bin/env bash
# 睿深行 AI 中台 · 生产备份脚本（PG 逻辑备份 + 命名卷快照级 tar）。
#
# 用法（在仓库根目录执行，需 docker 与 compose 项目已起）：
#   ./deploy/backup.sh                 # 默认备份到 ./backups/<时间戳>/
#   BACKUP_ROOT=/mnt/nas/backups ./deploy/backup.sh
#
# 说明：
#   - PostgreSQL：pg_dump 逻辑备份（可定点恢复单库/单表），gzip 落盘。
#   - qdrant/minio/redis/pgdata/caddy_data/backend_data：对命名卷做 tar 级快照。
#     真实生产建议对 qdrant 改用其 /collections/{c}/snapshots API 做一致性快照；
#     此处 tar 级作为兜底，足以覆盖重启/误删恢复。
#   - 任一组件未运行则该步跳过（skip），不影响其余组件备份。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# ── 预检：docker 必须在（容器化备份依赖守护 + compose 项目）──
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: 未找到 docker 命令，无法执行容器化备份。请安装 Docker 或在已装 Docker 的主机上运行。" >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker 守护未运行（docker info 失败）。请先启动 Docker Desktop / dockerd，" >&2
  echo "       并确保 'docker compose up -d' 已拉起生产栈，再执行本备份脚本。" >&2
  exit 3
fi

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$TS"
mkdir -p "$DEST"
LOG="$DEST/backup.log"
exec > >(tee -a "$LOG") 2>&1

echo "== 开始备份 @ $TS =="

# ── PostgreSQL 逻辑备份 ──
if docker compose ps postgres 2>/dev/null | grep -q "Up"; then
  docker compose exec -T postgres pg_dump -U app -d aiplat 2>/dev/null \
    | gzip > "$DEST/postgres.sql.gz" \
    && echo "postgres: ok ($(du -h "$DEST/postgres.sql.gz" | cut -f1))" \
    || echo "postgres: SKIP (pg_dump 失败)"
else
  echo "postgres: SKIP (容器未运行)"
fi

# ── 命名卷快照级 tar（动态发现，兼容自定义 compose 项目名）──
for vol in qdrant_storage minio_data redis_data pgdata caddy_data backend_data; do
  full="$(docker volume ls -q | grep -E "(^|/)${vol}$" | head -1)"
  if [ -n "$full" ]; then
    docker run --rm -v "${full}:/src" -v "$PWD/$DEST:/dest" alpine sh -c "cd /src && tar czf /dest/${vol}.tar.gz ." 2>/dev/null \
      && echo "$vol: ok ($(du -h "$DEST/${vol}.tar.gz" | cut -f1))" \
      || echo "$vol: SKIP (tar 失败，可能 alpine 镜像未拉取)"
  else
    echo "$vol: SKIP (卷不存在)"
  fi
done

echo "== 备份完成：$DEST =="
ls -lh "$DEST"
