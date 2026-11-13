"""运行配置：全部来自环境变量，见仓库根目录 .env.example。"""
from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv(
            "DATABASE_URL", "postgresql://report:report@postgres:5432/smartreportgen"
        )
        # 用于加密数据源密码/令牌（Fernet）。留空时首次启动自动生成并写入 sys.setting（不推荐生产使用）。
        self.secret_key = os.getenv("SECRET_KEY", "")
        # 向量化：hash = 内置离线词袋哈希向量（无语义，仅兜底）；openai = 任意 OpenAI 兼容 /embeddings 接口
        # （OpenAI、Ollama、Xinference、vLLM、DashScope 兼容模式、硅基流动等）
        self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "hash").strip().lower()
        # 也接受统一 AI 网关变量 AI_BASE_URL / AI_API_KEY（EMBEDDING_* 优先）
        self.embedding_base_url = (os.getenv("EMBEDDING_BASE_URL") or os.getenv("AI_BASE_URL") or "").rstrip("/")
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("AI_API_KEY") or ""
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "")
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1024"))
        self.embedding_send_dimensions = _bool("EMBEDDING_SEND_DIMENSIONS", False)
        self.embedding_timeout = float(os.getenv("EMBEDDING_TIMEOUT", "30"))
        # 检索查询的指令前缀（Qwen3-Embedding / E5 等指令式模型推荐；只作用于查询，不影响已入库向量）
        self.embedding_query_instruction = os.getenv("EMBEDDING_QUERY_INSTRUCTION", "").replace("\\n", "\n")

        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:18600").rstrip("/")
        self.query_max_rows = int(os.getenv("QUERY_MAX_ROWS", "50000"))
        self.query_timeout = int(os.getenv("QUERY_TIMEOUT_SECONDS", "60"))
        # MCP 写入查询和预览文件，管理 API 读取预览；容器部署时使用同一个私有卷。
        self.report_work_dir = os.getenv("REPORT_WORK_DIR", "/srv/report-work")
        self.seed_demo = _bool("SEED_DEMO", True)
        # 演示 API 数据源的内部地址（compose 网络内 api 服务）
        self.demo_api_base_url = os.getenv("DEMO_API_BASE_URL", "http://api:8000/demo-api")
        self.mcp_port = int(os.getenv("MCP_PORT", "8001"))
        self.api_port = int(os.getenv("API_PORT", "8000"))
        # 默认与管理台共用对外地址，网关按 /mcp 路径转发。
        self.mcp_public_url = os.getenv("MCP_PUBLIC_URL") or f"{self.public_base_url}/mcp"
        self.app_name = os.getenv("APP_NAME", "SmartReportGen")

        self.iam_host = os.getenv("HAIER_IAM_HOST", "https://iama.haier.net").strip().rstrip("/")
        # 浏览器跳转用的 IAM 地址（与服务端访问地址不同时才需要，如经内网代理访问 IAM）
        self.iam_authorize_host = os.getenv("HAIER_IAM_AUTHORIZE_HOST", "").strip().rstrip("/")
        self.iam_client_id = os.getenv("HAIER_IAM_CLIENT_ID", "").strip()
        self.iam_client_secret = os.getenv("HAIER_IAM_CLIENT_SECRET", "").strip()
        self.iam_scope = os.getenv("HAIER_IAM_SCOPE", "read").strip()
        # 需在 IAM 应用中登记的回调地址；留空 = {PUBLIC_BASE_URL}/auth/iam/callback
        self.iam_redirect_uri = os.getenv("IAM_REDIRECT_URI", "").strip()
        self.iam_timeout = float(os.getenv("IAM_TIMEOUT", "10"))
        self.iam_token_cache_seconds = int(os.getenv("IAM_TOKEN_CACHE_SECONDS", "300"))
        # IAM 登录后自动登记员工（无任何权限，待管理员授权）
        self.iam_auto_register = _bool("IAM_AUTO_REGISTER", True)
        # 引导管理员：这些工号首次经 IAM 登录时自动登记为管理员（逗号分隔）
        self.iam_bootstrap_admins = [x.strip() for x in os.getenv("IAM_BOOTSTRAP_ADMINS", "").split(",") if x.strip()]
        # 自动注册的员工默认角色（普通用户）。可对该角色授权，作为所有员工的基础数据权限
        self.default_user_role = os.getenv("DEFAULT_USER_ROLE", "user").strip()
        self.session_hours = max(1, int(os.getenv("SESSION_HOURS", "12")))

    @property
    def embedding_signature(self) -> str:
        if self.embedding_provider == "openai":
            return f"openai:{self.embedding_model}:{self.embedding_dim}"
        return f"hash:v1:{self.embedding_dim}"


settings = Settings()
