import hashlib
import mimetypes
import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Document, KnowledgeBase

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

ALLOWED_MIME_PREFIXES = ("text/",)
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/octet-stream",
}


def secure_filename(filename: str) -> str:
    # 只保留文件名部分并替换危险字符，避免用户上传路径穿越形式的文件名。
    raw_name = Path(filename or "").name.strip()
    if not raw_name:
        return "upload"
    safe = SAFE_NAME_RE.sub("_", raw_name).strip("._")
    return safe or "upload"


def validate_extension(filename: str) -> str:
    # 扩展名是第一层白名单，提前拒绝可执行文件等明显不支持的类型。
    settings = get_settings()
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_extensions:
        raise AppError(
            415,
            "unsupported_file_extension",
            "Unsupported file extension.",
            {"allowed_extensions": list(settings.allowed_extensions)},
        )
    return suffix


def validate_mime(content_type: str, extension: str) -> str:
    # MIME 由客户端提供，不完全可信；这里结合扩展名做宽松校验。
    # 某些浏览器/客户端会把文本文件传成 application/octet-stream，因此保留兼容分支。
    mime = (content_type or "").split(";")[0].strip().lower()
    guessed = mimetypes.types_map.get(extension, "")
    effective = mime or guessed or "application/octet-stream"
    if effective.startswith(ALLOWED_MIME_PREFIXES) or effective in ALLOWED_MIME_TYPES:
        return effective
    if extension in {".md", ".markdown", ".txt"} and effective == "application/octet-stream":
        return effective
    raise AppError(415, "unsupported_mime_type", "Unsupported MIME type.", {"mime_type": effective})


async def read_upload_bytes(upload: UploadFile) -> tuple[bytes, int, str]:
    # 分块读取上传内容，边读边计算 sha256，避免重复遍历文件内容。
    settings = get_settings()
    chunks = []
    total = 0
    sha = hashlib.sha256()
    while True:
        block = await upload.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > settings.max_upload_bytes:
            raise AppError(
                413,
                "file_too_large",
                "Uploaded file is too large.",
                {"max_upload_mb": settings.max_upload_mb},
            )
        sha.update(block)
        chunks.append(block)
    if total == 0:
        raise AppError(422, "empty_file", "Uploaded file is empty.")
    return b"".join(chunks), total, sha.hexdigest()


def duplicate_document_exists(db: Session, kb_id: str, sha256: str) -> bool:
    # 同一知识库内按内容 hash 去重；不同知识库允许保存同一份文件。
    existing = db.scalar(
        select(Document.id).where(
            Document.knowledge_base_id == kb_id,
            Document.sha256 == sha256,
            Document.status != "deleted",
        )
    )
    return existing is not None


def create_document_from_upload(
    db: Session,
    kb: KnowledgeBase,
    original_filename: str,
    content_type: str,
    content: bytes,
    file_size: int,
    sha256: str,
) -> Document:
    # 这个函数只负责落盘和创建 Document 记录，不做解析/切块/索引。
    # 这样上传存储和后续 RAG 处理可以清晰分层。
    safe_name = secure_filename(original_filename)
    extension = validate_extension(safe_name)
    mime_type = validate_mime(content_type, extension)
    if duplicate_document_exists(db, kb.id, sha256):
        raise AppError(409, "duplicate_document", "Document already exists in this knowledge base.")

    stored_filename = f"{uuid4().hex}{extension}"
    upload_dir = get_settings().upload_dir / kb.id
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = upload_dir / stored_filename
    storage_path.write_bytes(content)

    document = Document(
        knowledge_base_id=kb.id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        storage_path=str(storage_path),
        file_extension=extension,
        mime_type=mime_type,
        file_size=file_size,
        sha256=sha256,
        status="pending",
    )
    db.add(document)
    try:
        db.commit()
    except IntegrityError as exc:
        # 数据库唯一约束是并发上传时的最后防线；失败时要删除刚落盘的文件。
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise AppError(
            409, "duplicate_document", "Document already exists in this knowledge base."
        ) from exc
    db.refresh(document)
    return document
