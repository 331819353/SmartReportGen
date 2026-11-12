# 模型桥：Codex CLI 只支持 Responses API（wire_api = "responses"），而 AI 网关只提供 Chat Completions。
# LiteLLM 代理在两者之间转换协议。只在 compose 内部网络中使用，不映射端口。
FROM python:3.12-slim
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG LITELLM_VERSION=1.106.5
RUN pip install --no-cache-dir --retries 10 --timeout 60 -i ${PIP_INDEX_URL} "litellm[proxy]==${LITELLM_VERSION}"
COPY integrations/codex-docker/litellm.yaml /app/litellm.yaml
RUN useradd -m bridge
USER bridge
EXPOSE 4000
CMD ["litellm", "--config", "/app/litellm.yaml", "--host", "0.0.0.0", "--port", "4000"]
