import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # 所有配置都支持 RAG_ 前缀的环境变量覆盖，例如 RAG_DATABASE_URL。
    # 默认值刻意选择“本地可运行”：SQLite、mock embedding、mock generation、同步任务。
    app_name: str = "Local Enterprise RAG"
    environment: str = "local"
    debug: bool = False

    # data_dir 下保存 SQLite 数据库、上传原文件和可选的 ChromaDB 持久化目录。
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/app.db"
    redis_url: str = "redis://localhost:6379/0"
    # True 时 Celery 任务会在当前进程立即执行，便于本地开发和测试。
    # Docker Compose 中通常设为 false，让 Redis + worker 真正异步处理。
    celery_task_always_eager: bool = True
    # 可选外部 env 文件，用于复用其他项目里的 API key，避免复制密钥。
    external_env_file: str = ""

    # 上传安全边界：限制文件大小和允许的文档类型。
    max_upload_mb: int = 50
    allowed_extensions: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md", ".markdown")

    # 向量库后端 auto 会优先尝试 ChromaDB，不可用时退回本地扫描。
    vector_store_backend: str = Field(default="auto", description="auto, chroma, or local")
    # embedding_provider 决定 chunk 和 query 如何转成向量。
    # mock 只保证确定性，适合测试链路，不具备真实语义能力。
    embedding_provider: str = "mock"
    embedding_model: str = "mock-hash-embedding"
    embedding_dimension: int = 384

    # generation_provider 决定最终答案如何生成。
    # mock 会直接拼接证据，openai/openai-compatible 会调用聊天模型。
    generation_provider: str = "mock"
    chat_model: str = "mock-local-answerer"
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("RAG_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = Field(
        default="", validation_alias=AliasChoices("RAG_OPENAI_BASE_URL", "OPENAI_BASE_URL")
    )
    dashscope_api_key: str = Field(
        default="", validation_alias=AliasChoices("RAG_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY")
    )
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    rerank_provider: str = "lexical"
    rerank_model: str = ""
    rerank_api_key: str = ""
    rerank_base_url: str = "https://dashscope.aliyuncs.com"
    rerank_endpoint_path: str = "/compatible-api/v1/reranks"
    rerank_timeout_seconds: int = Field(default=10, gt=0)

    default_chunk_size: int = 800
    default_chunk_overlap: int = 120
    # RRF 的平滑参数，数值越大，排名差异造成的分数差越小。
    default_rrf_k: int = 60
    default_top_k: int = 8
    # 预留给真实 LLM 拼接上下文时限制证据长度，避免 prompt 过长。
    max_context_chars: int = 6000

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="RAG_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    # 配置读取会被缓存，避免每次请求都重复解析 env 文件。
    # 测试里会显式 cache_clear，以便不同测试使用不同临时数据库。
    _load_project_env_files()
    settings = Settings()
    settings.ensure_data_dirs()
    return settings


def _load_project_env_files() -> None:
    # 先加载项目 .env，再根据 RAG_EXTERNAL_ENV_FILE 加载外部配置。
    # _load_env_file 不会覆盖已经存在的环境变量，方便 CI 或命令行优先注入。
    project_env = PROJECT_ROOT / ".env"
    _load_env_file(project_env)
    external_env_file = os.environ.get("RAG_EXTERNAL_ENV_FILE", "").strip()
    if not external_env_file:
        return
    external_path = Path(external_env_file)
    if not external_path.is_absolute():
        external_path = PROJECT_ROOT / external_path
    _load_env_file(external_path)


def _load_env_file(path: Path) -> None:
    # 这里实现一个轻量 env loader，避免额外依赖 python-dotenv。
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
