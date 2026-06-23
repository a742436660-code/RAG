from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import DocumentChunk
from app.services.embeddings import cosine_similarity, get_embedding_service


@dataclass
class VectorSearchHit:
    # 向量检索只需要返回 chunk_id 和相似度分数，详细内容再从数据库回表读取。
    chunk_id: str
    score: float


class BaseVectorStore:
    # 向量库抽象层：检索服务不需要知道底层是 ChromaDB 还是本地 fallback。
    backend_name = "base"

    def upsert(
        self,
        db: Session,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        raise NotImplementedError

    def query(
        self,
        db: Session,
        knowledge_base_id: str,
        query: str,
        top_k: int,
    ) -> list[VectorSearchHit]:
        raise NotImplementedError

    def delete_document(self, db: Session, knowledge_base_id: str, document_id: str) -> None:
        raise NotImplementedError

    def delete_knowledge_base(self, db: Session, knowledge_base_id: str) -> None:
        raise NotImplementedError


class LocalVectorStore(BaseVectorStore):
    # 本地 fallback 不真正持久化向量，只在查询时重新计算 chunk embedding 并线性扫描。
    # 优点是零额外依赖；缺点是数据量变大后会明显变慢。
    backend_name = "local"

    def upsert(
        self,
        db: Session,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        # 本地模式没有外部向量库可写，只在 chunk 上标记 fallback 元信息。
        for chunk in chunks:
            chunk.chroma_collection_name = "local_fallback"
            chunk.chroma_vector_id = chunk.id
        db.flush()

    def query(
        self,
        db: Session,
        knowledge_base_id: str,
        query: str,
        top_k: int,
    ) -> list[VectorSearchHit]:
        # 查询时先把问题向量化，再和知识库内所有 chunk 逐个计算 cosine similarity。
        embedding_service = get_embedding_service()
        query_embedding = embedding_service.embed_texts([query])[0]
        chunks = list(
            db.scalars(
                select(DocumentChunk).where(DocumentChunk.knowledge_base_id == knowledge_base_id)
            ).all()
        )
        scored = [
            VectorSearchHit(
                chunk_id=chunk.id,
                score=cosine_similarity(
                    query_embedding,
                    embedding_service.embed_texts([chunk.content])[0],
                ),
            )
            for chunk in chunks
        ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def delete_document(self, db: Session, knowledge_base_id: str, document_id: str) -> None:
        return None

    def delete_knowledge_base(self, db: Session, knowledge_base_id: str) -> None:
        return None


class ChromaVectorStore(BaseVectorStore):
    # ChromaDB 后端负责持久化向量和 metadata，适合文档量更大的本地知识库。
    backend_name = "chroma"

    def __init__(self) -> None:
        # ChromaDB 是可选依赖；缺失时抛 AppError，由 auto 模式捕获后退回 local。
        try:
            import chromadb
        except ImportError as exc:
            raise AppError(
                500, "chromadb_missing", "Install chromadb or use local vector backend."
            ) from exc
        self._chromadb = chromadb
        settings = get_settings()
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(settings.chroma_dir))

    def collection_name(self, knowledge_base_id: str) -> str:
        # Chroma collection 名称不能直接包含 UUID 的连字符格式，这里转成安全名称。
        return "kb_" + knowledge_base_id.replace("-", "_")

    def _collection(self, knowledge_base_id: str):
        return self.client.get_or_create_collection(name=self.collection_name(knowledge_base_id))

    def upsert(
        self,
        db: Session,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        # 将 chunk 文本、embedding 和 metadata 一起写入 Chroma，后续可按 document_id 删除。
        if not chunks:
            return
        collection_name = self.collection_name(knowledge_base_id)
        collection = self._collection(knowledge_base_id)
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "knowledge_base_id": chunk.knowledge_base_id,
                    "document_id": chunk.document_id,
                    "source_filename": chunk.source_filename,
                }
                for chunk in chunks
            ],
        )
        for chunk in chunks:
            chunk.chroma_collection_name = collection_name
            chunk.chroma_vector_id = chunk.id
        db.flush()

    def query(
        self,
        db: Session,
        knowledge_base_id: str,
        query: str,
        top_k: int,
    ) -> list[VectorSearchHit]:
        # Chroma 返回 distance，这里转成越大越相似的 score，便于统一排序和日志展示。
        collection = self._collection(knowledge_base_id)
        embedding = get_embedding_service().embed_texts([query])[0]
        response = collection.query(query_embeddings=[embedding], n_results=top_k)
        ids = response.get("ids", [[]])[0]
        distances = response.get("distances", [[]])[0]
        hits = []
        for chunk_id, distance in zip(ids, distances):
            hits.append(VectorSearchHit(chunk_id=chunk_id, score=1.0 / (1.0 + float(distance))))
        return hits

    def delete_document(self, db: Session, knowledge_base_id: str, document_id: str) -> None:
        # 外部向量库删除失败不阻断主流程；下一次重建索引仍会 upsert 新向量。
        try:
            self._collection(knowledge_base_id).delete(where={"document_id": document_id})
        except Exception:
            return None

    def delete_knowledge_base(self, db: Session, knowledge_base_id: str) -> None:
        try:
            self.client.delete_collection(name=self.collection_name(knowledge_base_id))
        except Exception:
            return None


@lru_cache
def get_vector_store() -> BaseVectorStore:
    # 向量库实例会被缓存，避免每次查询重复初始化 Chroma client。
    settings = get_settings()
    backend = settings.vector_store_backend.lower()
    if backend == "local":
        return LocalVectorStore()
    if backend == "chroma":
        return ChromaVectorStore()
    if backend != "auto":
        raise AppError(500, "vector_backend_unsupported", "Unsupported vector store backend.")
    try:
        return ChromaVectorStore()
    except AppError:
        # auto 模式的设计目标是“即使没装 ChromaDB，MVP 也能完整跑通”。
        return LocalVectorStore()
