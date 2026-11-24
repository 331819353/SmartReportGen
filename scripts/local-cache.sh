#!/usr/bin/env bash
# SmartReportGen 本地构建缓存与离线优先发版工具。
#
# 目标：首次联网将基础镜像、系统底座（含 Chromium 与中文字体）和 Python wheel
# 保存到本地目录；后续发版默认直接复用本地缓存，仅在新增或升级依赖包时增量联网并落盘。
#
# 用法：
#   scripts/local-cache.sh <命令> [选项] [-- 透传给 docker compose up 的参数]
#
# 命令：
#   up       同步本地缓存 -> 用本地缓存构建应用镜像 -> docker compose up -d --no-build（推荐发版）
#   build    同步本地缓存 -> 用本地缓存构建应用镜像 smartreportgen:latest
#   sync     仅同步/补齐本地缓存（基础镜像、底座镜像归档与增量 Python wheel）
#   load     仅从本地归档目录加载镜像到 Docker（不联网，适合清理 Docker 后恢复）
#   status   查看本地缓存状态（镜像归档、wheel 数量与离线满足情况）
#
# 选项：
#   --cache-dir DIR     本地缓存目录（默认：<仓库>/local-cache，也可用 LOCAL_CACHE_DIR）
#   --platform PLAT     目标平台（默认：当前宿主机架构，如 linux/amd64；也可用 TARGET_PLATFORM）
#   --pip-index URL     PyPI 镜像源（默认：https://pypi.org/simple，也可用 PIP_INDEX_URL）
#   --apt-mirror HOST   首次构建底座时的 Debian APT 镜像域名（如 mirrors.aliyun.com，也可用 APT_MIRROR）
#   --offline           纯离线模式：禁止任何联网拉取或下载，缺项直接报错
#   --refresh-base      强制重新联网构建并覆盖本地底座镜像（升级 Chromium/系统安全补丁时使用）
#   --no-save           不导出/更新镜像 .tar.gz 归档（仅使用 Docker 引擎本地镜像）
#   -h, --help          显示帮助
set -euo pipefail

export DOCKER_BUILDKIT=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$ROOT/app/requirements.txt}"

PYTHON_IMAGE="python:3.12-slim"
PG_IMAGE="pgvector/pgvector:pg16"
NGINX_IMAGE="nginx:stable-alpine"
BUILDER_BASE_IMAGE="smartreportgen-builder-base:py3.12"
RUNTIME_BASE_IMAGE="smartreportgen-runtime-base:py3.12"
APP_IMAGE="${APP_IMAGE:-smartreportgen:latest}"

UPSTREAM_IMAGES=(
  "$PYTHON_IMAGE"
  "$PG_IMAGE"
  "$NGINX_IMAGE"
)

ALL_CACHED_IMAGES=(
  "$PYTHON_IMAGE"
  "$PG_IMAGE"
  "$NGINX_IMAGE"
  "$BUILDER_BASE_IMAGE"
  "$RUNTIME_BASE_IMAGE"
)

log() {
  printf '[local-cache] %s\n' "$*"
}

warn() {
  printf '[local-cache] 警告: %s\n' "$*" >&2
}

die() {
  printf '[local-cache] 错误: %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '2,25p' "$0"
}

# 安全读取 .env 中的指定键值，不执行脚本，防止特殊字符引发副作用。
read_env_var() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  awk -v k="$key" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      sub(/^[[:space:]]*export[[:space:]]+/, "", line)
      idx = index(line, "=")
      if (idx == 0) next
      name = substr(line, 1, idx - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name != k) next
      val = substr(line, idx + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
      if ((substr(val, 1, 1) == "\"" && substr(val, length(val), 1) == "\"") ||
          (substr(val, 1, 1) == "'"'"'" && substr(val, length(val), 1) == "'"'"'")) {
        val = substr(val, 2, length(val) - 2)
      }
      out = val
    }
    END { if (out != "") print out }
  ' "$ENV_FILE"
}

detect_platform() {
  case "$(uname -m)" in
    x86_64|amd64) echo "linux/amd64" ;;
    aarch64|arm64) echo "linux/arm64" ;;
    *) die "未识别的 CPU 架构：$(uname -m)，请通过 --platform 手动指定（如 linux/amd64）" ;;
  esac
}

# 读取默认配置：显式环境变量 > .env > 内置默认
CACHE_DIR="${LOCAL_CACHE_DIR:-$(read_env_var LOCAL_CACHE_DIR)}"
[ -n "$CACHE_DIR" ] || CACHE_DIR="$ROOT/local-cache"

PLATFORM="${TARGET_PLATFORM:-$(read_env_var TARGET_PLATFORM)}"
[ -n "$PLATFORM" ] || PLATFORM="$(detect_platform)"

PIP_INDEX_URL="${PIP_INDEX_URL:-$(read_env_var PIP_INDEX_URL)}"
[ -n "$PIP_INDEX_URL" ] || PIP_INDEX_URL="https://pypi.org/simple"

APT_MIRROR="${APT_MIRROR:-$(read_env_var APT_MIRROR)}"
OFFLINE="${PIP_OFFLINE:-0}"
REFRESH_BASE=0
SAVE_ARCHIVES=1
COMMAND=""
COMPOSE_EXTRA_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    up|build|sync|load|status)
      [ -z "$COMMAND" ] || die "重复指定命令：$1（已指定 $COMMAND）"
      COMMAND="$1"
      ;;
    --cache-dir)
      shift
      CACHE_DIR="${1:?--cache-dir 需要目录参数}"
      ;;
    --platform)
      shift
      PLATFORM="${1:?--platform 需要平台参数，如 linux/amd64}"
      ;;
    --pip-index)
      shift
      PIP_INDEX_URL="${1:?--pip-index 需要 URL 参数}"
      ;;
    --apt-mirror)
      shift
      APT_MIRROR="${1:?--apt-mirror 需要镜像域名参数}"
      ;;
    --offline)
      OFFLINE=1
      ;;
    --refresh-base)
      REFRESH_BASE=1
      ;;
    --no-save)
      SAVE_ARCHIVES=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      COMPOSE_EXTRA_ARGS=("$@")
      break
      ;;
    *)
      die "未知参数：$1（使用 --help 查看用法）"
      ;;
  esac
  shift
done

[ -n "$COMMAND" ] || { usage; exit 2; }

# 统一转换为绝对路径，兼容 --cache-dir 传入相对路径或外部绝对路径
case "$CACHE_DIR" in
  /*) ;;
  *) CACHE_DIR="$ROOT/$CACHE_DIR" ;;
esac

PLAT_SLUG="${PLATFORM//\//-}"
IMAGES_DIR="$CACHE_DIR/images/$PLAT_SLUG"
WHEELS_DIR="$CACHE_DIR/wheels/${PLAT_SLUG}-py312"

TMP_ARCHIVE=""
cleanup_tmp() {
  if [ -n "$TMP_ARCHIVE" ] && [ -e "$TMP_ARCHIVE" ]; then
    rm -f "$TMP_ARCHIVE"
  fi
}
trap cleanup_tmp EXIT

require_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker 命令，请先安装 Docker Engine"
  docker info >/dev/null 2>&1 || die "Docker 守护进程不可用，请确认服务已启动且当前用户有权限"
}

image_archive_path() {
  local img="$1"
  local safe="${img//\//__}"
  safe="${safe//:/--}"
  printf '%s/%s.tar.gz' "$IMAGES_DIR" "$safe"
}

image_platform() {
  local img="$1"
  docker image inspect -f '{{.Os}}/{{.Architecture}}' "$img" 2>/dev/null || true
}

image_ready() {
  local img="$1"
  local got
  got="$(image_platform "$img")"
  [ "$got" = "$PLATFORM" ]
}

compress_to_file() {
  local dest="$1"
  if command -v pigz >/dev/null 2>&1; then
    pigz -1 > "$dest"
  else
    gzip -1 > "$dest"
  fi
}

decompress_file() {
  local src="$1"
  if command -v pigz >/dev/null 2>&1; then
    pigz -dc "$src"
  else
    gzip -dc "$src"
  fi
}

save_image_archive() {
  local img="$1"
  local archive
  archive="$(image_archive_path "$img")"
  mkdir -p "$IMAGES_DIR"
  TMP_ARCHIVE="${archive}.tmp.$$"
  log "保存镜像归档：${img} -> ${archive}"
  docker save "$img" | compress_to_file "$TMP_ARCHIVE"
  mv -f "$TMP_ARCHIVE" "$archive"
  TMP_ARCHIVE=""
}

load_image_archive() {
  local img="$1"
  local archive
  archive="$(image_archive_path "$img")"
  [ -f "$archive" ] || return 1
  log "从本地归档加载镜像：${archive} -> ${img}"
  decompress_file "$archive" | docker load >/dev/null
  image_ready "$img" || die "已加载 ${archive}，但镜像 ${img} 架构与目标 ${PLATFORM} 不一致（当前：$(image_platform "$img")）"
}

ensure_upstream_image() {
  local img="$1"
  local archive
  archive="$(image_archive_path "$img")"

  if image_ready "$img"; then
    log "复用 Docker 本地镜像：${img}（${PLATFORM}）"
    if [ "$SAVE_ARCHIVES" = "1" ] && [ ! -f "$archive" ]; then
      save_image_archive "$img"
    fi
    return 0
  fi

  if [ -f "$archive" ]; then
    load_image_archive "$img"
    return 0
  fi

  if [ "$OFFLINE" = "1" ]; then
    die "离线模式下缺少镜像 ${img}（${PLATFORM}），且未找到本地归档 ${archive}。请先在联网环境执行：scripts/local-cache.sh sync --platform ${PLATFORM}"
  fi

  local existing
  existing="$(image_platform "$img")"
  if [ -n "$existing" ] && [ "$existing" != "$PLATFORM" ]; then
    warn "Docker 中已有 ${img}（${existing}），与目标平台 ${PLATFORM} 不同，将拉取目标平台镜像"
  fi

  log "首次联网拉取镜像：${img}（${PLATFORM}）"
  docker pull --platform "$PLATFORM" "$img"
  if [ "$SAVE_ARCHIVES" = "1" ]; then
    save_image_archive "$img"
  fi
}

ensure_base_target() {
  local target="$1"
  local img="$2"
  local archive
  archive="$(image_archive_path "$img")"

  if [ "$REFRESH_BASE" != "1" ]; then
    if image_ready "$img"; then
      log "复用 Docker 本地底座镜像：${img}（${PLATFORM}）"
      if [ "$SAVE_ARCHIVES" = "1" ] && [ ! -f "$archive" ]; then
        save_image_archive "$img"
      fi
      return 0
    fi

    if [ -f "$archive" ]; then
      load_image_archive "$img"
      return 0
    fi
  fi

  if [ "$OFFLINE" = "1" ]; then
    die "离线模式下缺少底座镜像 ${img}（${PLATFORM}），且未找到本地归档 ${archive}。请先在联网环境执行：scripts/local-cache.sh sync --platform ${PLATFORM}"
  fi

  log "构建底座镜像：${img}（target=${target}，platform=${PLATFORM}）"
  docker build \
    --platform "$PLATFORM" \
    --pull=false \
    -f "$ROOT/app/Dockerfile.base" \
    --target "$target" \
    --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" \
    --build-arg "APT_MIRROR=$APT_MIRROR" \
    -t "$img" \
    "$ROOT"

  if [ "$SAVE_ARCHIVES" = "1" ]; then
    save_image_archive "$img"
  fi
}

wheels_satisfy_requirements() {
  [ -d "$WHEELS_DIR" ] || return 1
  docker run --rm \
    --platform "$PLATFORM" \
    -v "$REQUIREMENTS_FILE:/tmp/requirements.txt:ro" \
    -v "$WHEELS_DIR:/wheels:ro" \
    "$BUILDER_BASE_IMAGE" \
    pip install --dry-run --no-index --find-links=/wheels -r /tmp/requirements.txt >/dev/null 2>&1
}

sync_wheels() {
  mkdir -p "$WHEELS_DIR"

  if wheels_satisfy_requirements; then
    local count
    count="$(find "$WHEELS_DIR" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) | wc -l | tr -d ' ')"
    log "本地 wheel 缓存已完全满足 app/requirements.txt（共 ${count} 个文件，0 联网下载）"
    return 0
  fi

  if [ "$OFFLINE" = "1" ]; then
    warn "本地 wheel 缓存（${WHEELS_DIR}）未能满足 app/requirements.txt，诊断如下："
    docker run --rm \
      --platform "$PLATFORM" \
      -v "$REQUIREMENTS_FILE:/tmp/requirements.txt:ro" \
      -v "$WHEELS_DIR:/wheels:ro" \
      "$BUILDER_BASE_IMAGE" \
      pip install --dry-run --no-index --find-links=/wheels -r /tmp/requirements.txt || true
    die "离线模式下无法联网补齐缺失依赖，请先联网运行：scripts/local-cache.sh sync --platform ${PLATFORM}"
  fi

  log "检测到首次初始化或依赖变更，优先复用本地 wheel，仅联网下载新增/缺失包..."
  local host_uid="" host_gid=""
  if [ "$(uname -s)" = "Linux" ]; then
    host_uid="$(id -u)"
    host_gid="$(id -g)"
  fi

  docker run -i --rm \
    --platform "$PLATFORM" \
    -e "PIP_INDEX_URL=$PIP_INDEX_URL" \
    -e "HOST_UID=$host_uid" \
    -e "HOST_GID=$host_gid" \
    -v "$REQUIREMENTS_FILE:/tmp/requirements.txt:ro" \
    -v "$WHEELS_DIR:/wheels" \
    "$BUILDER_BASE_IMAGE" \
    python - <<'PY'
import os
import pathlib
import re
import subprocess
import sys

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
from pip._vendor.packaging.version import Version

wheels_dir = pathlib.Path("/wheels")
req_file = pathlib.Path("/tmp/requirements.txt")
constraints_file = pathlib.Path("/tmp/local-constraints.txt")
pip_index_url = os.environ["PIP_INDEX_URL"]

before_files = {p.name for p in wheels_dir.iterdir() if p.is_file()}

# 1. 扫描本地已有的 wheel 文件，按规范包名记录全部已有版本。
local_versions: dict[str, list[Version]] = {}
for p in wheels_dir.glob("*.whl"):
    try:
        name, ver, _, _ = parse_wheel_filename(p.name)
        local_versions.setdefault(canonicalize_name(name), []).append(ver)
    except Exception:
        parts = p.stem.split("-")
        if len(parts) >= 2:
            try:
                local_versions.setdefault(canonicalize_name(parts[0]), []).append(Version(parts[1]))
            except Exception:
                continue

# 2. 解析 requirements.txt 中的直接依赖，判断本地已有版本是否仍满足要求。
direct_specs: dict[str, Requirement] = {}
for raw in req_file.read_text(encoding="utf-8").splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    try:
        req = Requirement(line)
    except Exception:
        continue
    direct_specs[canonicalize_name(req.name)] = req

# 3. 为本地已有且兼容的包生成版本约束，避免新增一个包时顺带重下所有旧传递依赖。
constraints: list[str] = []
for name, versions in sorted(local_versions.items()):
    if name in direct_specs:
        matching = [v for v in versions if v in direct_specs[name].specifier]
        if matching:
            constraints.append(f"{name}=={max(matching)}")
    else:
        constraints.append(f"{name}=={max(versions)}")

base_cmd = [
    sys.executable, "-m", "pip", "wheel",
    "--prefer-binary",
    "--retries", "10",
    "--timeout", "60",
    "--find-links", str(wheels_dir),
    "-i", pip_index_url,
    "--wheel-dir", str(wheels_dir),
    "-r", str(req_file),
]

rc = 0
if constraints:
    constraints_file.write_text("\n".join(constraints) + "\n", encoding="utf-8")
    print(f"[local-cache] 已锁定 {len(constraints)} 个本地已有包版本，增量同步缺失依赖...", flush=True)
    rc = subprocess.call(base_cmd + ["-c", str(constraints_file)])
    if rc != 0:
        print("[local-cache] 新增包要求升级部分已有传递依赖，解除旧版本锁定后重试...", flush=True)
        rc = subprocess.call(base_cmd)
else:
    print("[local-cache] 本地 wheel 目录为空，执行首次全量下载与打包...", flush=True)
    rc = subprocess.call(base_cmd)

if rc != 0:
    sys.exit(rc)

after_files = {p.name for p in wheels_dir.iterdir() if p.is_file()}
added = sorted(after_files - before_files)
if added:
    print(f"[local-cache] 本次新增落盘 {len(added)} 个文件：", flush=True)
    for item in added:
        print(f"  + {item}", flush=True)
else:
    print("[local-cache] 未新增文件（全部复用本地已有 wheel）", flush=True)

# 避免在 Linux 宿主机留下 root 属主的新文件，影响普通用户清理缓存。
host_uid = os.environ.get("HOST_UID", "")
host_gid = os.environ.get("HOST_GID", "")
if host_uid.isdigit() and host_gid.isdigit():
    uid, gid = int(host_uid), int(host_gid)
    for name in added:
        try:
            os.chown(wheels_dir / name, uid, gid)
        except OSError:
            pass
PY

  if ! wheels_satisfy_requirements; then
    docker run --rm \
      --platform "$PLATFORM" \
      -v "$REQUIREMENTS_FILE:/tmp/requirements.txt:ro" \
      -v "$WHEELS_DIR:/wheels:ro" \
      "$BUILDER_BASE_IMAGE" \
      pip install --dry-run --no-index --find-links=/wheels -r /tmp/requirements.txt || true
    die "同步后离线自检未通过，请检查上面的 pip 输出"
  fi
  log "本地 wheel 缓存同步完成并通过离线自检（目录：${WHEELS_DIR}）"
}

cmd_sync() {
  require_docker
  local img
  for img in "${UPSTREAM_IMAGES[@]}"; do
    ensure_upstream_image "$img"
  done
  ensure_base_target "builder-base" "$BUILDER_BASE_IMAGE"
  ensure_base_target "runtime-base" "$RUNTIME_BASE_IMAGE"
  sync_wheels
}

cmd_load() {
  require_docker
  local img archive loaded=0 skipped=0 missing=0
  for img in "${ALL_CACHED_IMAGES[@]}"; do
    archive="$(image_archive_path "$img")"
    if image_ready "$img"; then
      log "跳过（Docker 已存在 ${PLATFORM} 镜像）：${img}"
      skipped=$((skipped + 1))
    elif [ -f "$archive" ]; then
      load_image_archive "$img"
      loaded=$((loaded + 1))
    else
      warn "未找到本地归档：${archive}"
      missing=$((missing + 1))
    fi
  done
  log "镜像恢复完成：新加载 ${loaded} 个，已存在跳过 ${skipped} 个，缺失 ${missing} 个"
  [ "$missing" -eq 0 ] || die "存在缺失的镜像归档，请先在联网环境执行 scripts/local-cache.sh sync --platform ${PLATFORM}"
}

cmd_build() {
  cmd_sync
  log "使用本地底座镜像与本地 wheel 离线构建 ${APP_IMAGE}（${PLATFORM}）..."
  docker build \
    --platform "$PLATFORM" \
    --pull=false \
    -f "$ROOT/app/Dockerfile" \
    --build-context "wheels=$WHEELS_DIR" \
    --build-arg "BUILDER_BASE_IMAGE=$BUILDER_BASE_IMAGE" \
    --build-arg "RUNTIME_BASE_IMAGE=$RUNTIME_BASE_IMAGE" \
    --build-arg "PIP_INDEX_URL=$PIP_INDEX_URL" \
    --build-arg "PIP_OFFLINE=1" \
    -t "$APP_IMAGE" \
    "$ROOT"
  log "构建完成：${APP_IMAGE}"
}

cmd_up() {
  cmd_build
  log "启动/更新服务（docker compose up -d --no-build）..."
  (
    cd "$ROOT"
    if [ "${#COMPOSE_EXTRA_ARGS[@]}" -gt 0 ]; then
      docker compose up -d --no-build "${COMPOSE_EXTRA_ARGS[@]}"
    else
      docker compose up -d --no-build
    fi
  )
}

human_size() {
  local path="$1"
  if [ -e "$path" ]; then
    du -sh "$path" 2>/dev/null | awk '{print $1}'
  else
    echo "-"
  fi
}

cmd_status() {
  require_docker
  printf '目标平台: %s\n' "$PLATFORM"
  printf '缓存目录: %s（总计 %s）\n' "$CACHE_DIR" "$(human_size "$CACHE_DIR")"
  printf 'PyPI 源 : %s\n\n' "$PIP_INDEX_URL"

  printf '镜像缓存（目录：%s）：\n' "$IMAGES_DIR"
  local img archive docker_state archive_state
  for img in "${ALL_CACHED_IMAGES[@]}"; do
    archive="$(image_archive_path "$img")"
    if image_ready "$img"; then
      docker_state="已就绪"
    else
      local got
      got="$(image_platform "$img")"
      if [ -n "$got" ]; then
        docker_state="架构不匹配(${got})"
      else
        docker_state="未加载"
      fi
    fi
    if [ -f "$archive" ]; then
      archive_state="已归档($(human_size "$archive"))"
    else
      archive_state="未归档"
    fi
    printf '  - %-40s Docker: %-16s 本地文件: %s\n' "$img" "$docker_state" "$archive_state"
  done

  printf '\nPython wheel 缓存（目录：%s）：\n' "$WHEELS_DIR"
  local wheel_count=0
  if [ -d "$WHEELS_DIR" ]; then
    wheel_count="$(find "$WHEELS_DIR" -maxdepth 1 -type f \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) | wc -l | tr -d ' ')"
  fi
  printf '  - 文件数量: %s（占用 %s）\n' "$wheel_count" "$(human_size "$WHEELS_DIR")"
  if image_ready "$BUILDER_BASE_IMAGE"; then
    if wheels_satisfy_requirements; then
      printf '  - 依赖覆盖: 已完全满足 app/requirements.txt（可直接离线构建）\n'
    elif [ "$wheel_count" -eq 0 ]; then
      printf '  - 依赖覆盖: 尚未初始化（运行 scripts/local-cache.sh sync 即可首次缓存）\n'
    else
      printf '  - 依赖覆盖: 存在新增或变更依赖（运行 scripts/local-cache.sh sync/build 将仅增量联网补齐）\n'
    fi
  elif [ "$wheel_count" -eq 0 ]; then
    printf '  - 依赖覆盖: 尚未初始化（运行 scripts/local-cache.sh sync 即可首次缓存）\n'
  else
    printf '  - 依赖覆盖: 底座镜像尚未加载（运行 scripts/local-cache.sh load 后可离线校验）\n'
  fi
}

case "$COMMAND" in
  sync)   cmd_sync ;;
  load)   cmd_load ;;
  build)  cmd_build ;;
  up)     cmd_up ;;
  status) cmd_status ;;
esac
