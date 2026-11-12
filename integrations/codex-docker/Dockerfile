# Codex CLI + smart-report 技能，用于在容器中连接 SmartReportGen MCP 做端到端测试。
# 构建（在仓库根目录）：docker compose --profile codex build codex
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE}

# npm 镜像（默认国内 npmmirror，可用 --build-arg NPM_REGISTRY=... 覆盖）
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG CODEX_VERSION=0.167.2
RUN npm config set registry "${NPM_REGISTRY}" \
 && npm install -g "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force \
 && apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行 agent
RUN useradd -m -s /bin/bash agent
USER agent
WORKDIR /home/agent/work

# 技能：Codex 用户级目录（~/.codex/skills 与 ~/.agents/skills）
COPY --chown=agent:agent skills/ /home/agent/.codex/skills/
COPY --chown=agent:agent skills/ /home/agent/.agents/skills/
# 工作目录中的 AGENTS.md 告诉 Codex 使用技能与 MCP
COPY --chown=agent:agent AGENTS.md /home/agent/work/AGENTS.md
# Codex 配置：模型（OpenAI 兼容网关）+ MCP 服务；密钥全部来自环境变量
COPY --chown=agent:agent integrations/codex-docker/config.toml /home/agent/.codex/config.toml
RUN git init -q /home/agent/work

ENTRYPOINT ["codex"]
CMD ["--help"]
