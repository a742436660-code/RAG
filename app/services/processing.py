from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.fts import delete_document_fts, index_chunks_fts
from app.db.models import BackgroundTask, Document, DocumentChunk
from app.db.session import get_sessionmaker
from app.services.chunking import chunk_document, metadata_to_json
from app.services.embeddings import get_embedding_service
from app.services.parsers import parse_document
from app.services.vector_store import get_vector_store

STAGE_PROGRESS = {
    # 文档处理的粗粒度进度，供 /documents/{id}/status 和前端展示。
    "validating": 5,
    "parsing": 20,
    "chunking": 35,
    "saving_chunks": 50,
    "indexing_fts": 65,
    "embedding": 78,
    "indexing_vectors": 90,
    "verifying": 96,
    "completed": 100,
}


def run_document_processing(document_id: str, reindex: bool = False) -> None:
    # 文档入库后的主处理流程：解析 -> 切块 -> FTS 索引 -> embedding -> 向量索引。
    # reindex 当前不改变分支逻辑，因为每次处理都会先清理旧索引再重建。
    db = get_sessionmaker()()
    task = None
    current_stage = "validating"
    try:
        document = db.get(Document, document_id)
        if document is None:
            raise AppError(404, "document_not_found", "Document not found.")
        task = get_latest_task(db, document_id)
        update_stage(db, document, task, "validating")
        # 原始文件是后续重试和重建索引的基础，丢失时不能继续处理。
        path = Path(document.storage_path)
        if not path.exists():
            raise AppError(404, "stored_file_missing", "Stored upload file is missing.")

        document.status = "processing"
        document.failed_stage = None
        document.error_message = None
        document.processing_started_at = datetime.utcnow()
        document.processing_finished_at = None
        db.commit()

        clear_document_indexes(db, document)

        current_stage = "parsing"
        update_stage(db, document, task, current_stage)
        # 解析器把不同文件格式统一成 ParsedDocument/DocumentElement。
        parsed = parse_document(path, document.file_extension)
        document.page_count = parsed.page_count

        current_stage = "chunking"
        update_stage(db, document, task, current_stage)
        # 切块时使用知识库级别的 chunk 参数，保证同一知识库内索引粒度一致。
        chunks = chunk_document(
            parsed,
            chunk_size=document.knowledge_base.chunk_size,
            chunk_overlap=document.knowledge_base.chunk_overlap,
        )
        if not chunks:
            raise AppError(422, "no_chunks_created", "Document produced no chunks.")

        current_stage = "saving_chunks"
        update_stage(db, document, task, current_stage)
        # 先把 chunk 写入数据库并 flush 出 ID，后续 FTS 和向量库都依赖 chunk.id。
        db_chunks = [
            DocumentChunk(
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                element_type=chunk.element_type,
                source_filename=document.original_filename,
                metadata_json=metadata_to_json(chunk.metadata),
            )
            for chunk in chunks
        ]
        db.add_all(db_chunks)
        db.flush()

        current_stage = "indexing_fts"
        update_stage(db, document, task, current_stage)
        # 关键词检索索引：用于 sparse retrieval 和 hybrid 的一半召回。
        index_chunks_fts(db, db_chunks)

        current_stage = "embedding"
        update_stage(db, document, task, current_stage)
        # embedding 把 chunk 文本转为向量，是 dense retrieval 的基础。
        embeddings = get_embedding_service().embed_texts([chunk.content for chunk in db_chunks])

        current_stage = "indexing_vectors"
        update_stage(db, document, task, current_stage)
        # ChromaDB 或本地 fallback 保存/标记向量索引结果。
        get_vector_store().upsert(db, document.knowledge_base_id, db_chunks, embeddings)

        current_stage = "verifying"
        update_stage(db, document, task, current_stage)
        document.chunk_count = len(db_chunks)
        document.status = "completed"
        document.processing_finished_at = datetime.utcnow()
        if task is not None:
            task.status = "completed"
            task.current_stage = "completed"
            task.progress = STAGE_PROGRESS["completed"]
        db.commit()
    except Exception as exc:
        db.rollback()
        # 失败时记录阶段和错误，再继续抛出异常；eager 模式下测试能直接看到错误。
        mark_failed(db, document_id, task, current_stage, exc)
        raise
    finally:
        db.close()


def get_latest_task(db: Session, document_id: str) -> BackgroundTask:
    # 大多数情况下 upload/retry/reindex 已创建任务；如果缺失则补建，保证状态可记录。
    task = db.scalar(
        select(BackgroundTask)
        .where(BackgroundTask.document_id == document_id)
        .order_by(BackgroundTask.created_at.desc())
    )
    if task is None:
        task = BackgroundTask(
            document_id=document_id, task_type="process_document", status="pending"
        )
        db.add(task)
        db.flush()
    return task


def update_stage(
    db: Session,
    document: Document,
    task: BackgroundTask,
    stage: str,
) -> None:
    # 每进入一个处理阶段就落库，前端轮询时可以看到实时进度。
    task.status = "processing"
    task.current_stage = stage
    task.progress = STAGE_PROGRESS.get(stage, 0)
    document.status = "processing"
    db.commit()


def clear_document_indexes(db: Session, document: Document) -> None:
    # 重建索引必须先清理旧的 FTS、向量和 chunk，避免旧证据被继续检索到。
    delete_document_fts(db, document.id)
    get_vector_store().delete_document(db, document.knowledge_base_id, document.id)
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    document.chunk_count = 0
    db.commit()


def mark_failed(
    db: Session,
    document_id: str,
    task: Optional[BackgroundTask],
    failed_stage: str,
    exc: Exception,
) -> None:
    # 失败状态写回 Document 和 BackgroundTask，便于用户决定 retry 或检查文件。
    document = db.get(Document, document_id)
    if document is not None:
        document.status = "failed"
        document.failed_stage = failed_stage
        document.error_message = str(exc)
        document.processing_finished_at = datetime.utcnow()
    if task is not None:
        task.status = "failed"
        task.current_stage = failed_stage
        task.error_message = str(exc)
    db.commit()
