from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.fts import delete_document_fts
from app.db.models import BackgroundTask, Document
from app.services.knowledge_bases import get_knowledge_base_or_404
from app.services.storage import create_document_from_upload, read_upload_bytes
from app.services.vector_store import get_vector_store
from app.workers.tasks import enqueue_document_processing


def get_document_or_404(db: Session, document_id: str) -> Document:
    # deleted 文档对外表现为不存在，避免已删除资源继续被查询或操作。
    document = db.get(Document, document_id)
    if document is None or document.status == "deleted":
        raise AppError(404, "document_not_found", "Document not found.")
    return document


def list_documents(db: Session, kb_id: str) -> list[Document]:
    # 先确认知识库存在，再列出未删除文档，避免无效 kb_id 返回空列表造成误解。
    get_knowledge_base_or_404(db, kb_id)
    return list(
        db.scalars(
            select(Document)
            .where(Document.knowledge_base_id == kb_id, Document.status != "deleted")
            .order_by(Document.created_at.desc())
        ).all()
    )


async def upload_document(db: Session, kb_id: str, upload: UploadFile) -> Document:
    # 上传链路：保存原文件和元数据 -> 创建后台任务 -> 触发文档处理。
    kb = get_knowledge_base_or_404(db, kb_id)
    original_filename = upload.filename or "upload"
    content = await read_upload_bytes(upload)
    file_bytes, file_size, sha256 = content
    document = create_document_from_upload(
        db=db,
        kb=kb,
        original_filename=original_filename,
        content_type=upload.content_type or "",
        content=file_bytes,
        file_size=file_size,
        sha256=sha256,
    )
    task = BackgroundTask(
        # BackgroundTask 让前端即使不依赖 Celery result backend，也能查询处理进度。
        document_id=document.id,
        task_type="process_document",
        status="pending",
        current_stage="pending",
    )
    db.add(task)
    db.commit()
    task_id = enqueue_document_processing(document.id)
    document.task_id = task_id
    task.celery_task_id = task_id
    db.commit()
    db.refresh(document)
    return document


def get_document_status(db: Session, document_id: str) -> tuple[Document, Optional[BackgroundTask]]:
    # 返回最新任务，适合前端轮询展示 processing stage/progress。
    document = get_document_or_404(db, document_id)
    task = db.scalar(
        select(BackgroundTask)
        .where(BackgroundTask.document_id == document_id)
        .order_by(BackgroundTask.created_at.desc())
    )
    return document, task


def retry_document(db: Session, document_id: str) -> Document:
    document = get_document_or_404(db, document_id)
    # 只有失败或仍 pending 的文档可 retry；completed 文档应使用 reindex。
    if document.status not in {"failed", "pending"}:
        raise AppError(
            409, "document_not_retryable", "Only failed or pending documents can be retried."
        )
    document.retry_count += 1
    task = BackgroundTask(
        document_id=document.id,
        task_type="retry_document",
        status="pending",
        current_stage="pending",
    )
    db.add(task)
    db.commit()
    task_id = enqueue_document_processing(document.id)
    document.task_id = task_id
    task.celery_task_id = task_id
    db.commit()
    db.refresh(document)
    return document


def reindex_document(db: Session, document_id: str) -> Document:
    document = get_document_or_404(db, document_id)
    # reindex 会基于已保存的原文件重新构建所有索引，不需要用户再次上传。
    task = BackgroundTask(
        document_id=document.id,
        task_type="reindex_document",
        status="pending",
        current_stage="pending",
    )
    db.add(task)
    db.commit()
    task_id = enqueue_document_processing(document.id, reindex=True)
    document.task_id = task_id
    task.celery_task_id = task_id
    db.commit()
    db.refresh(document)
    return document


def delete_document(db: Session, document_id: str) -> None:
    document = get_document_or_404(db, document_id)
    document.status = "deleting"
    db.commit()
    # 删除顺序：先清外部索引，再删原文件，最后删数据库记录。
    # 这样即使中途失败，状态也能提示用户该文档正在删除。
    delete_document_fts(db, document.id)
    get_vector_store().delete_document(db, document.knowledge_base_id, document.id)
    storage_path = Path(document.storage_path)
    if storage_path.exists():
        storage_path.unlink()
    document.status = "deleted"
    db.delete(document)
    db.commit()
