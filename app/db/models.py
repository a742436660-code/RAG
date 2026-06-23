from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    # 统一用 UUID 字符串做主键，方便跨表引用，也避免暴露自增 ID 的业务规模。
    return str(uuid4())


class TimestampMixin:
    # 常见审计字段：创建时间和更新时间由数据库默认值/SQLAlchemy onupdate 维护。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBase(TimestampMixin, Base):
    # 知识库是文档集合的边界，也保存该集合的切块和 embedding 配置。
    # 当前项目是单用户，所以没有 user_id/tenant_id。
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(100), default="mock", nullable=False)
    embedding_model: Mapped[str] = mapped_column(
        String(200), default="mock-hash-embedding", nullable=False
    )
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=384, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=120, nullable=False)

    documents: Mapped[list["Document"]] = relationship(
        # 删除知识库时级联删除文档和 chunk，避免孤儿数据。
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(TimestampMixin, Base):
    # Document 保存“原始上传文件”的元信息和处理状态。
    # 真正参与检索的是后续生成的 DocumentChunk。
    __tablename__ = "documents"
    __table_args__ = (
        # 同一知识库内通过文件内容 hash 去重；不同知识库可上传相同文件。
        UniqueConstraint("knowledge_base_id", "sha256", name="uq_documents_kb_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    # failed_stage/error_message 记录处理失败的位置，便于用户 retry 或排查解析问题。
    failed_stage: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    processing_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentChunk.chunk_index",
    )
    background_tasks: Mapped[list["BackgroundTask"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(TimestampMixin, Base):
    # Chunk 是 RAG 检索的最小证据单元：FTS5、向量库、引用都围绕 chunk 工作。
    __tablename__ = "document_chunks"
    __table_args__ = (
        # chunk_index 保证同一文档内顺序稳定；content_hash 防止同一文档内重复片段。
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        UniqueConstraint("document_id", "content_hash", name="uq_chunks_document_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    element_type: Mapped[str] = mapped_column(String(80), default="text", nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # 当使用 ChromaDB 时记录 collection/vector id；本地 fallback 会写入占位值。
    chroma_collection_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    chroma_vector_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(TimestampMixin, Base):
    # 一次聊天会话归属于一个知识库，后续消息会按 created_at 排序返回。
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    # Message 同时保存用户问题和助手回答；助手消息会携带引用和检索日志 ID。
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    retrieval_log_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("retrieval_logs.id", ondelete="SET NULL"), nullable=True
    )
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class RetrievalLog(Base):
    # RetrievalLog 是 RAG 可观测性的核心：完整记录 sparse/dense/fusion/rerank/final。
    # 之后排查“为什么没搜到”或“为什么答案引用这个片段”主要看这张表。
    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    dense_results_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    sparse_results_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    fusion_results_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    rerank_results_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    final_evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    fallback_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieval_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    generation_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class BackgroundTask(TimestampMixin, Base):
    # BackgroundTask 记录文档处理任务进度，不依赖 Celery result backend 也能给前端展示状态。
    __tablename__ = "background_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_stage: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="background_tasks")
