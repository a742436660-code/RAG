import hashlib
import math

from app.core.config import get_settings
from app.core.errors import AppError


class EmbeddingService:
    # EmbeddingService 屏蔽不同 provider 的调用差异，检索层只关心“文本 -> 向量”。
    def __init__(self, provider: str, model: str, dimension: int) -> None:
        self.provider = provider
        self.model = model
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # mock 用于测试闭环；openai-compatible/dashscope 用统一 OpenAI SDK 调用。
        if self.provider == "mock":
            return [hash_embedding(text, self.dimension) for text in texts]
        if self.provider in {"openai", "openai-compatible", "dashscope"}:
            return self._embed_openai(texts)
        raise AppError(500, "embedding_provider_unsupported", "Unsupported embedding provider.")

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        # DashScope 走 OpenAI 兼容接口时，需要替换 api_key 和 base_url。
        settings = get_settings()
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url or None
        if self.provider == "dashscope":
            api_key = settings.dashscope_api_key or settings.openai_api_key
            base_url = settings.dashscope_base_url or settings.openai_base_url or None
        if not api_key:
            raise AppError(
                500,
                "embedding_api_key_missing",
                "Embedding API key is required for the configured provider.",
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AppError(
                500, "openai_package_missing", "Install openai to use this provider."
            ) from exc
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        # DashScope 的 OpenAI 兼容 embeddings 接口限制单次 input 不能超过 10 条。
        # 这里统一分批请求，避免上传稍长文档时一次性 chunk 太多导致 400。
        batch_size = 10 if self.provider == "dashscope" else 100
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = client.embeddings.create(model=self.model, input=batch)
            embeddings.extend(item.embedding for item in response.data)
        return embeddings


def get_embedding_service() -> EmbeddingService:
    # 每次按当前 Settings 构造，测试切换环境变量后能生效。
    settings = get_settings()
    return EmbeddingService(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )


def hash_embedding(text: str, dimension: int) -> list[float]:
    # 确定性 hash embedding：相同文本必然得到相同向量，便于测试。
    # 它不具备真实语义相似能力，生产检索应使用真实 embedding 模型。
    digest = hashlib.shake_256(text.encode("utf-8")).digest(dimension * 4)
    values = []
    for index in range(dimension):
        raw = int.from_bytes(digest[index * 4 : index * 4 + 4], "big", signed=False)
        values.append((raw / 2**32) * 2 - 1)
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    # 向量相似度：向量越同向，分数越高；输入为空时返回 0。
    if not left or not right:
        return 0.0
    limit = min(len(left), len(right))
    return sum(left[i] * right[i] for i in range(limit))
