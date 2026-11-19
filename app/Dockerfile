# 构建上下文为仓库根目录：docker build -f app/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /srv

# 使用镜像内 Chromium 验收离线报告，MCP 客户端无需安装浏览器。
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt .
RUN pip install --retries 10 --timeout 60 -i ${PIP_INDEX_URL} -r requirements.txt

COPY app/smartreportgen ./smartreportgen
# Skill 单一来源：仓库根目录 skills/（MCP 通过 get_skill_guide / get_template_spec / smart_report 提示词对外提供）
COPY skills/smart-report ./smartreportgen/docs/skill
COPY docs/HTTP_API.md ./smartreportgen/docs/HTTP_API.md

RUN useradd -r -u 10001 -m app && mkdir -m 700 /srv/report-work && chown -R app /srv
USER app
# jieba 词典缓存预热（写入用户临时目录）
RUN python -c "import jieba; jieba.initialize()"
EXPOSE 8000 8001
CMD ["uvicorn", "smartreportgen.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
