from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

"""
1. 错误处理（1个）
ErrorResponse：统一错误响应格式
2. 知识库管理（4个）
KnowledgeBaseCreate：创建知识库时的请求数据验证
KnowledgeBaseUpdate：更新知识库时的请求数据验证
KnowledgeBaseRead：读取知识库的响应数据格式
KnowledgeBaseStats：知识库统计信息（文档数量、分块数量等）
3. 文档管理（2个）
DocumentRead：文档详细信息响应（包含处理状态、失败原因等）
DocumentStatus：文档状态+关联的后台任务信息
4. 后台任务（1个）
BackgroundTaskRead：后台任务信息（Celery任务状态、进度等）
5. 搜索功能（3个）
SearchRequest：搜索请求数据验证
SearchResult：单个搜索结果（包含chunk、评分、排名）
SearchResponse：搜索完整响应（包含查询结果列表和日志ID）
6. 对话功能（5个）
ChatRequest：聊天请求数据验证
ChatResponse：聊天响应（答案+引用+会话ID）
Citation：引用来源信息
MessageRead：单条消息格式
ConversationRead：完整会话（包含消息列表）
7. 检索日志（1个）
RetrievalLogRead：检索过程详细日志（各阶段结果、延迟时间等）

"""


class ErrorResponse(BaseModel):
    # 统一错误响应结构：code 给程序判断，message 给用户展示，details 放调试上下文。
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None


class KnowledgeBaseCreate(BaseModel):
    # 创建知识库时可覆盖默认 embedding 和切块配置；未传则使用 Settings 默认值。
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = Field(default=None, ge=1)
    chunk_size: Optional[int] = Field(default=None, ge=100, le=4000)
    chunk_overlap: Optional[int] = Field(default=None, ge=0, le=1000)


class KnowledgeBaseUpdate(BaseModel):
    # 只允许修改展示信息和切块参数；embedding 配置变更通常需要更完整的重建策略。
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    chunk_size: Optional[int] = Field(default=None, ge=100, le=4000)
    chunk_overlap: Optional[int] = Field(default=None, ge=0, le=1000)


class KnowledgeBaseRead(BaseModel):
    id: str
    name: str
    description: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    chunk_size: int
    chunk_overlap: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeBaseStats(BaseModel):
    knowledge_base_id: str
    document_count: int
    completed_document_count: int
    failed_document_count: int
    chunk_count: int


class BackgroundTaskRead(BaseModel):
    id: str
    document_id: str
    celery_task_id: Optional[str]
    task_type: str
    status: str
    progress: int
    current_stage: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentRead(BaseModel):
    # 文档响应包含处理状态和失败原因，前端可据此展示进度、retry 或 reindex 操作。
    id: str
    knowledge_base_id: str
    original_filename: str
    stored_filename: str
    storage_path: str
    file_extension: str
    mime_type: str
    file_size: int
    sha256: str
    status: str
    failed_stage: Optional[str]
    error_message: Optional[str]
    page_count: Optional[int]
    chunk_count: int
    task_id: Optional[str]
    retry_count: int
    processing_started_at: Optional[datetime]
    processing_finished_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentStatus(BaseModel):
    document: DocumentRead
    task: Optional[BackgroundTaskRead] = None


class SearchRequest(BaseModel):
    # retrieval_mode 控制检索策略：关键词、向量、混合或混合后重排。
    query: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    retrieval_mode: Literal["sparse", "dense", "hybrid", "hybrid_rerank"] = "hybrid_rerank"


class SearchResult(BaseModel):
    # SearchResult 是最终给用户/LLM 的证据片段，quote 是用于展示的短摘录。
    chunk_id: str
    document_id: str
    source_filename: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    content: str
    quote: str
    score: float
    source: Literal["sparse", "dense", "both"]
    rank: int


class SearchResponse(BaseModel):
    query: str
    retrieval_mode: str
    results: list[SearchResult]
    log_id: str
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


class Citation(BaseModel):
    # Citation 是答案里的引用单位，必须能回溯到具体 chunk 和原文件。
    citation_id: int
    chunk_id: str
    document_id: str
    source_filename: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    quote: str


class ChatRequest(BaseModel):
    # conversation_id 为空时创建新会话；传入时会把问答追加到已有会话。
    query: str = Field(min_length=1)
    conversation_id: Optional[str] = None
    top_k: int = Field(default=8, ge=1, le=50)
    retrieval_mode: Literal["sparse", "dense", "hybrid", "hybrid_rerank"] = "hybrid_rerank"


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    conversation_id: str
    retrieval_log_id: str
    refusal: bool
    model_name: str


class MessageRead(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    citations: list[dict[str, Any]]
    retrieval_log_id: Optional[str]
    model_name: Optional[str]
    latency_ms: Optional[int]
    created_at: datetime


class ConversationRead(BaseModel):
    id: str
    knowledge_base_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRead] = Field(default_factory=list)


class RetrievalLogRead(BaseModel):
    # 读取检索日志时，把数据库里的 JSON 字符串还原成各阶段结果列表。
    id: str
    request_id: Optional[str]
    knowledge_base_id: str
    conversation_id: Optional[str]
    query: str
    retrieval_mode: str
    dense_results: list[dict[str, Any]]
    sparse_results: list[dict[str, Any]]
    fusion_results: list[dict[str, Any]]
    rerank_results: list[dict[str, Any]]
    final_evidence: list[dict[str, Any]]
    fallback_used: bool
    fallback_reason: Optional[str]
    retrieval_latency_ms: Optional[int]
    generation_latency_ms: Optional[int]
    total_latency_ms: Optional[int]
    model_name: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: datetime
