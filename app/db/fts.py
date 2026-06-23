from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import DocumentChunk


def ensure_fts(engine: Engine) -> None:
    # FTS5 虚拟表不由 SQLAlchemy ORM 直接管理，因此需要单独创建。
    # content/source_filename/section_title 会被建立全文索引，其他 ID 字段只用于回表。
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts
            USING fts5(
                chunk_id UNINDEXED,
                knowledge_base_id UNINDEXED,
                document_id UNINDEXED,
                content,
                source_filename,
                section_title,
                tokenize='unicode61'
            )
            """
        )


def index_chunks_fts(db: Session, chunks: Sequence[DocumentChunk]) -> None:
    # 重新索引某个文档时，先删掉该文档旧的 FTS 记录，再批量插入新 chunk。
    # 这里不 commit，由外层处理流程统一管理事务边界。
    if not chunks:
        return
    db.execute(
        text(
            """
            DELETE FROM document_chunks_fts
            WHERE document_id = :document_id
            """
        ),
        {"document_id": chunks[0].document_id},
    )
    rows = [
        {
            "chunk_id": chunk.id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "document_id": chunk.document_id,
            "content": chunk.content,
            "source_filename": chunk.source_filename,
            "section_title": chunk.section_title or "",
        }
        for chunk in chunks
    ]
    db.execute(
        text(
            """
            INSERT INTO document_chunks_fts(
                chunk_id,
                knowledge_base_id,
                document_id,
                content,
                source_filename,
                section_title
            )
            VALUES (
                :chunk_id,
                :knowledge_base_id,
                :document_id,
                :content,
                :source_filename,
                :section_title
            )
            """
        ),
        rows,
    )


def delete_document_fts(db: Session, document_id: str) -> None:
    # 删除或重建文档索引时使用，确保 FTS 虚拟表不会残留旧 chunk。
    db.execute(
        text("DELETE FROM document_chunks_fts WHERE document_id = :document_id"),
        {"document_id": document_id},
    )


def delete_knowledge_base_fts(db: Session, knowledge_base_id: str) -> None:
    # 删除整个知识库时，先按知识库清空 FTS 记录，再删除 ORM 表数据。
    db.execute(
        text("DELETE FROM document_chunks_fts WHERE knowledge_base_id = :knowledge_base_id"),
        {"knowledge_base_id": knowledge_base_id},
    )


def sanitize_fts_query(query: str) -> str:
    # FTS5 的 MATCH 语法对引号、操作符比较敏感。
    # 这里把用户输入拆成词并加引号，再用 OR 放宽匹配，避免简单查询因语法报错失败。
    query = query.replace('"', " ").strip()
    terms = [term for term in query.split() if term]
    if not terms:
        return query
    return " OR ".join(f'"{term}"' for term in terms)
