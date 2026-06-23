from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.fts import delete_knowledge_base_fts
from app.db.models import Document, DocumentChunk, KnowledgeBase
from app.schemas.api import KnowledgeBaseCreate, KnowledgeBaseStats, KnowledgeBaseUpdate
from app.services.vector_store import get_vector_store


def get_knowledge_base_or_404(db: Session, kb_id: str) -> KnowledgeBase:
    # 服务层统一使用这个函数取知识库，保证不存在时返回稳定的业务错误。
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise AppError(404, "knowledge_base_not_found", "Knowledge base not found.")
    return kb


def create_knowledge_base(db: Session, payload: KnowledgeBaseCreate) -> KnowledgeBase:
    settings = get_settings()
    # 知识库保存切块参数，后续上传到该知识库的文档都会按这里的配置处理。
    chunk_size = payload.chunk_size or settings.default_chunk_size
    chunk_overlap = payload.chunk_overlap or settings.default_chunk_overlap
    if chunk_overlap >= chunk_size:
        raise AppError(
            422, "invalid_chunk_config", "chunk_overlap must be smaller than chunk_size."
        )

    kb = KnowledgeBase(
        name=payload.name.strip(),
        description=payload.description or "",
        embedding_provider=payload.embedding_provider or settings.embedding_provider,
        embedding_model=payload.embedding_model or settings.embedding_model,
        embedding_dimension=payload.embedding_dimension or settings.embedding_dimension,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def list_knowledge_bases(db: Session) -> list[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())).all())


def update_knowledge_base(db: Session, kb_id: str, payload: KnowledgeBaseUpdate) -> KnowledgeBase:
    kb = get_knowledge_base_or_404(db, kb_id)
    update_data = payload.model_dump(exclude_unset=True)
    # 修改切块参数时必须保持 overlap 小于 size，否则切块循环可能无法前进。
    if "chunk_overlap" in update_data or "chunk_size" in update_data:
        new_size = update_data.get("chunk_size", kb.chunk_size)
        new_overlap = update_data.get("chunk_overlap", kb.chunk_overlap)
        if new_overlap >= new_size:
            raise AppError(
                422, "invalid_chunk_config", "chunk_overlap must be smaller than chunk_size."
            )
    for key, value in update_data.items():
        if key == "name" and value is not None:
            value = value.strip()
        if value is not None:
            setattr(kb, key, value)
    db.commit()
    db.refresh(kb)
    return kb


def delete_knowledge_base(db: Session, kb_id: str) -> None:
    kb = get_knowledge_base_or_404(db, kb_id)
    # ORM 级联只能删除关系表，FTS5 和 ChromaDB 这类外部索引需要显式清理。
    get_vector_store().delete_knowledge_base(db, kb_id)
    delete_knowledge_base_fts(db, kb_id)
    db.delete(kb)
    db.commit()


def get_knowledge_base_stats(db: Session, kb_id: str) -> KnowledgeBaseStats:
    # stats 给前端仪表盘使用：文档数、成功/失败数和 chunk 总数。
    get_knowledge_base_or_404(db, kb_id)
    document_count = db.scalar(
        select(func.count(Document.id)).where(Document.knowledge_base_id == kb_id)
    )
    completed_count = db.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == kb_id,
            Document.status == "completed",
        )
    )
    failed_count = db.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == kb_id,
            Document.status == "failed",
        )
    )
    chunk_count = db.scalar(
        select(func.count(DocumentChunk.id)).where(DocumentChunk.knowledge_base_id == kb_id)
    )
    return KnowledgeBaseStats(
        knowledge_base_id=kb_id,
        document_count=int(document_count or 0),
        completed_document_count=int(completed_count or 0),
        failed_document_count=int(failed_count or 0),
        chunk_count=int(chunk_count or 0),
    )
