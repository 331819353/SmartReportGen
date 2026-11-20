# 构建上下文为仓库根目录：docker build -f app/Dockerfile .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

FROM base AS dependencies
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY app/requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
# jieba 搜索分词只用主词典和 finalseg。去除词性标注、关键词提取及 Paddle 模型，保留原有分词行为。
# 仅裁掉 Playwright 自带 Node 的非运行符号。最终镜像只复制处理后的依赖，不带构建工具。
RUN pip install --retries 10 --timeout 60 -i "${PIP_INDEX_URL}" --prefix /opt/python -r requirements.txt \
    && packages="$(python -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "/opt/python"}))')" \
    && rm -rf "$packages/jieba/analyse" "$packages/jieba/posseg" "$packages/jieba/lac_small" \
    && strip --strip-unneeded "$packages/playwright/driver/node"

FROM base
# 保留完整 Chromium 和中日韩字体用于桌面、窄屏及离线报告验收。
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /opt/python/ /usr/local/

# 运行用户仅需写入临时目录和报告工作卷，避免递归 chown 在镜像层中再保存一份源码。
RUN useradd -r -u 10001 -m app && install -d -m 700 -o app -g app /srv/report-work

COPY app/smartreportgen ./smartreportgen
# Skill 单一来源：仓库根目录 skills/（MCP 通过 get_skill_guide / get_template_spec / smart_report 提示词对外提供）
COPY skills/smart-report ./smartreportgen/docs/skill
COPY docs/HTTP_API.md ./smartreportgen/docs/HTTP_API.md

USER app
# jieba 词典缓存预热（写入用户临时目录）
RUN python -c "import jieba; jieba.initialize()"
EXPOSE 8000 8001
CMD ["uvicorn", "smartreportgen.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
