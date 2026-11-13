#!/usr/bin/env bash
# 把 skills/ 下的技能安装到各 agent 的用户级技能目录。
#
# 用法：
#   scripts/install-skill.sh [--link] [--dest DIR] [agent ...]
#     agent:  claude  -> ~/.claude/skills
#             codex   -> ~/.codex/skills（以及 ~/.agents/skills，新版 Codex 的用户级目录）
#             agents  -> ~/.agents/skills（遵循 agentskills 约定的其他 agent）
#             all     -> 以上全部（默认）
#     --link  创建软链接而不是复制（技能随仓库更新；仓库移动后链接会失效）
#     --dest  安装到指定目录（可多次使用），例如某个 agent 的自定义技能目录
#   SKILL=smart-report 可指定要安装的技能（默认安装 skills/ 下全部技能）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT/skills"
MODE=copy
DESTS=()
AGENTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --link) MODE=link ;;
    --dest) shift; DESTS+=("${1:?--dest 需要目录参数}") ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) AGENTS+=("$1") ;;
  esac
  shift
done
[ ${#AGENTS[@]} -eq 0 ] && [ ${#DESTS[@]} -eq 0 ] && AGENTS=(all)

for a in ${AGENTS[@]+"${AGENTS[@]}"}; do
  case "$a" in
    claude) DESTS+=("$HOME/.claude/skills") ;;
    codex)  DESTS+=("$HOME/.codex/skills" "$HOME/.agents/skills") ;;
    agents) DESTS+=("$HOME/.agents/skills") ;;
    all)    DESTS+=("$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills") ;;
    *) echo "未知 agent：${a}（可选 claude / codex / agents / all）" >&2; exit 2 ;;
  esac
done

if [ -n "${SKILL:-}" ]; then
  SKILLS=("$SKILL")
else
  SKILLS=()
  for d in "$SRC_DIR"/*/; do [ -f "$d/SKILL.md" ] && SKILLS+=("$(basename "$d")"); done
fi

# 去重目标目录
UNIQUE=()
for d in "${DESTS[@]}"; do
  case " ${UNIQUE[*]-} " in *" $d "*) ;; *) UNIQUE+=("$d") ;; esac
done

for dest in "${UNIQUE[@]}"; do
  mkdir -p "$dest"
  for s in "${SKILLS[@]}"; do
    src="$SRC_DIR/$s"
    [ -f "$src/SKILL.md" ] || { echo "跳过：$src 不是技能目录" >&2; continue; }
    target="$dest/$s"
    if [ -L "$target" ]; then
      rm "$target"                       # 旧的软链接：直接替换
    elif [ -e "$target" ]; then
      backup="$target.bak.$(date +%Y%m%d%H%M%S)"
      mv "$target" "$backup"             # 已有目录：先备份，不覆盖
      echo "已备份原有 $target -> $backup"
    fi
    if [ "$MODE" = link ]; then
      ln -s "$src" "$target"
    else
      cp -R "$src" "$target"
    fi
    echo "已安装 $s -> ${target}（${MODE}）"
  done
done

cat <<'EOF'

下一步：为 agent 注册 MCP 服务。Claude Code 使用 Bearer 令牌，Codex 使用海尔 IAM 浏览器登录。
  export SMARTREPORT_TOKEN=<海尔 IAM access token>
  Claude Code: claude mcp add --transport http smartreportgen http://localhost:18600/mcp --header "Authorization: Bearer $SMARTREPORT_TOKEN"
  Codex:       codex mcp add smartreportgen --url http://localhost:18600/mcp --oauth-client-id smartreportgen-cli
               codex mcp login smartreportgen
  其他 agent:  见 integrations/README.md
EOF
