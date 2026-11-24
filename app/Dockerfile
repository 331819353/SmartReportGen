# 构建上下文为仓库根目录：docker build -f app/Dockerfile .
ARG BASE_IMAGE=python:3.12-slim
ARG BUILDER_BASE_IMAGE=${BASE_IMAGE}
ARG RUNTIME_BASE_IMAGE=${BASE_IMAGE}

# 默认占位上下文：未通过 --build-context wheels=<本地目录> 覆盖时为空目录，不影响原生构建。
FROM scratch AS wheels

FROM ${BUILDER_BASE_IMAGE} AS dependencies
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_OFFLINE=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# 放在 COPY requirements.txt 之前，避免依赖变动导致 binutils 层失效；预制 builder 底座已含 strip，自动跳过。
RUN command -v strip >/dev/null 2>&1 \
    || (apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*)

COPY app/requirements.txt .

# jieba 搜索分词只用主词典和 finalseg。去除词性标注、关键词提取及 Paddle 模型，保留原有分词行为。
# 仅裁掉 Playwright 自带 Node 的非运行符号。最终镜像只复制处理后的依赖，不带构建工具。
# 优先从挂载的本地 wheels 离线安装；非离线模式若本地缺包，再回退混合模式仅联网补下缺失包。
RUN --mount=type=bind,from=wheels,source=.,target=/tmp/wheels \
    --mount=type=cache,target=/root/.cache/pip \
    sh -eu -c ' \
        has_local=0; \
        for f in /tmp/wheels/*.whl /tmp/wheels/*.tar.gz /tmp/wheels/*.zip; do \
            if [ -e "$f" ]; then has_local=1; break; fi; \
        done; \
        if [ "${PIP_OFFLINE}" = "1" ]; then \
            pip install --no-index --find-links=/tmp/wheels --prefix /opt/python -r requirements.txt; \
        elif [ "$has_local" = "1" ]; then \
            if ! pip install --no-index --find-links=/tmp/wheels --prefix /opt/python -r requirements.txt; then \
                echo "本地 wheel 未完全覆盖依赖，改用混合模式：优先本地缓存，仅联网下载缺失包..." >&2; \
                rm -rf /opt/python; \
                env -u PIP_NO_CACHE_DIR pip install --cache-dir=/root/.cache/pip --prefer-binary \
                    --retries 10 --timeout 60 --find-links=/tmp/wheels -i "${PIP_INDEX_URL}" \
                    --prefix /opt/python -r requirements.txt; \
            fi; \
        else \
            env -u PIP_NO_CACHE_DIR pip install --cache-dir=/root/.cache/pip --prefer-binary \
                --retries 10 --timeout 60 -i "${PIP_INDEX_URL}" \
                --prefix /opt/python -r requirements.txt; \
        fi; \
        packages="$(python -c "import sysconfig; print(sysconfig.get_path(\"purelib\", vars={\"base\": \"/opt/python\"}))")"; \
        rm -rf "$packages/jieba/analyse" "$packages/jieba/posseg" "$packages/jieba/lac_small"; \
        strip --strip-unneeded "$packages/playwright/driver/node" \
    '

FROM ${RUNTIME_BASE_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

# 保留完整 Chromium 和中日韩字体用于桌面、窄屏及离线报告验收。
# 使用预制 runtime 底座时已安装，自动跳过联网安装。
RUN (command -v chromium >/dev/null 2>&1 && dpkg -s fonts-noto-cjk >/dev/null 2>&1) \
    || (apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk && rm -rf /var/lib/apt/lists/*)

COPY --from=dependencies /opt/python/ /usr/local/

# 运行用户仅需写入临时目录和报告工作卷，避免递归 chown 在镜像层中再保存一份源码。
RUN useradd -r -u 10001 -m app && install -d -m 700 -o app -g app /srv/report-work

COPY app/smartreportgen ./smartreportgen
# Skill 单一来源：仓库根目录 skills/（MCP 通过 get_skill_guide / smart_report 提示词对外提供）
COPY skills/smart-report ./smartreportgen/docs/skill
COPY docs/HTTP_API.md ./smartreportgen/docs/HTTP_API.md

USER app
# jieba 词典缓存预热（写入用户临时目录）
RUN python -c "import jieba; jieba.initialize()"
EXPOSE 8000 8001
CMD ["uvicorn", "smartreportgen.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
