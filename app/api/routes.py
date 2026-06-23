from typing import Optional

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.session import get_engine
from app.schemas.api import (
    BackgroundTaskRead,
    ChatRequest,
    ChatResponse,
    ConversationRead,
    DocumentRead,
    DocumentStatus,
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseStats,
    KnowledgeBaseUpdate,
    RetrievalLogRead,
    SearchRequest,
    SearchResponse,
)
from app.services.chat import chat_with_knowledge_base
from app.services.conversations import get_conversation, list_conversations
from app.services.documents import (
    delete_document,
    get_document_or_404,
    get_document_status,
    list_documents,
    reindex_document,
    retry_document,
    upload_document,
)
from app.services.knowledge_bases import (
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base_or_404,
    get_knowledge_base_stats,
    list_knowledge_bases,
    update_knowledge_base,
)
from app.services.logs import get_retrieval_log
from app.services.retrieval import search_knowledge_base

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    # 轻量存活检查：不访问数据库，只说明应用进程还在。
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, str]:
    # 就绪检查：执行一条数据库查询，确认 API 依赖的存储层可用。
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.post("/knowledge-bases", response_model=KnowledgeBaseRead, status_code=201)
def create_kb(payload: KnowledgeBaseCreate, db: Session = Depends(get_db)) -> KnowledgeBaseRead:
    # 创建知识库时会固化 chunk 和 embedding 配置，后续文档按这些配置处理。
    return KnowledgeBaseRead.model_validate(create_knowledge_base(db, payload))


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseRead])
def list_kbs(db: Session = Depends(get_db)) -> list[KnowledgeBaseRead]:
    return [KnowledgeBaseRead.model_validate(item) for item in list_knowledge_bases(db)]


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseRead)
def get_kb(kb_id: str, db: Session = Depends(get_db)) -> KnowledgeBaseRead:
    return KnowledgeBaseRead.model_validate(get_knowledge_base_or_404(db, kb_id))


@router.patch("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseRead)
def patch_kb(
    kb_id: str,
    payload: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
) -> KnowledgeBaseRead:
    return KnowledgeBaseRead.model_validate(update_knowledge_base(db, kb_id, payload))


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_kb(kb_id: str, db: Session = Depends(get_db)) -> None:
    delete_knowledge_base(db, kb_id)


@router.get("/knowledge-bases/{kb_id}/stats", response_model=KnowledgeBaseStats)
def kb_stats(kb_id: str, db: Session = Depends(get_db)) -> KnowledgeBaseStats:
    return get_knowledge_base_stats(db, kb_id)


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentRead, status_code=201)
async def upload_kb_document(
    kb_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
) -> DocumentRead:
    # 上传接口只接收原文件；解析、切块、索引由服务层创建后台任务完成。
    return DocumentRead.model_validate(await upload_document(db, kb_id, file))


@router.get("/knowledge-bases/{kb_id}/documents", response_model=list[DocumentRead])
def list_kb_documents(kb_id: str, db: Session = Depends(get_db)) -> list[DocumentRead]:
    return [DocumentRead.model_validate(item) for item in list_documents(db, kb_id)]


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentRead:
    return DocumentRead.model_validate(get_document_or_404(db, document_id))


@router.get("/documents/{document_id}/status", response_model=DocumentStatus)
def document_status(document_id: str, db: Session = Depends(get_db)) -> DocumentStatus:
    # 前端轮询这个接口即可展示文档处理进度和失败原因。
    document, task = get_document_status(db, document_id)
    return DocumentStatus(
        document=DocumentRead.model_validate(document),
        task=BackgroundTaskRead.model_validate(task) if task is not None else None,
    )


@router.post("/documents/{document_id}/retry", response_model=DocumentRead)
def retry_doc(document_id: str, db: Session = Depends(get_db)) -> DocumentRead:
    # retry 只允许 failed/pending 文档，避免对已完成文档误触发重复处理。
    return DocumentRead.model_validate(retry_document(db, document_id))


@router.post("/documents/{document_id}/reindex", response_model=DocumentRead)
def reindex_doc(document_id: str, db: Session = Depends(get_db)) -> DocumentRead:
    # reindex 会重新解析原文件并重建 chunk、FTS 和向量索引。
    return DocumentRead.model_validate(reindex_document(db, document_id))


@router.delete("/documents/{document_id}", status_code=204)
def delete_doc(document_id: str, db: Session = Depends(get_db)) -> None:
    delete_document(db, document_id)


@router.post("/knowledge-bases/{kb_id}/search", response_model=SearchResponse)
def search_kb(
    kb_id: str,
    payload: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
    # 搜索接口只返回证据片段，不生成自然语言答案，适合调试检索质量。
    return search_knowledge_base(
        db=db,
        knowledge_base_id=kb_id,
        query=payload.query,
        top_k=payload.top_k,
        retrieval_mode=payload.retrieval_mode,
    )


@router.post("/knowledge-bases/{kb_id}/chat", response_model=ChatResponse)
def chat_kb(
    kb_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    # 聊天接口复用同一套检索链路，再基于证据生成回答和 citation。
    return chat_with_knowledge_base(
        db=db,
        knowledge_base_id=kb_id,
        query=payload.query,
        top_k=payload.top_k,
        retrieval_mode=payload.retrieval_mode,
        conversation_id=payload.conversation_id,
    )


@router.get("/conversations", response_model=list[ConversationRead])
def conversations(
    knowledge_base_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[ConversationRead]:
    return list_conversations(db, knowledge_base_id=knowledge_base_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationRead:
    return get_conversation(db, conversation_id)


@router.get("/retrieval-logs/{log_id}", response_model=RetrievalLogRead)
def retrieval_log(log_id: str, db: Session = Depends(get_db)) -> RetrievalLogRead:
    # 检索日志用于排查一次搜索/问答的每一步候选结果和耗时。
    return get_retrieval_log(db, log_id)
