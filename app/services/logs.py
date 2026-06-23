from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import RetrievalLog
from app.schemas.api import RetrievalLogRead
from app.services.json_utils import loads_json


def get_retrieval_log(db: Session, log_id: str) -> RetrievalLogRead:
    # RetrievalLog 中多个阶段结果以 JSON 字符串存储，读取时统一反序列化。
    log = db.get(RetrievalLog, log_id)
    if log is None:
        raise AppError(404, "retrieval_log_not_found", "Retrieval log not found.")
    return RetrievalLogRead(
        id=log.id,
        request_id=log.request_id,
        knowledge_base_id=log.knowledge_base_id,
        conversation_id=log.conversation_id,
        query=log.query,
        retrieval_mode=log.retrieval_mode,
        dense_results=loads_json(log.dense_results_json, []),
        sparse_results=loads_json(log.sparse_results_json, []),
        fusion_results=loads_json(log.fusion_results_json, []),
        rerank_results=loads_json(log.rerank_results_json, []),
        final_evidence=loads_json(log.final_evidence_json, []),
        fallback_used=bool(log.fallback_used),
        fallback_reason=log.fallback_reason,
        retrieval_latency_ms=log.retrieval_latency_ms,
        generation_latency_ms=log.generation_latency_ms,
        total_latency_ms=log.total_latency_ms,
        model_name=log.model_name,
        error_code=log.error_code,
        error_message=log.error_message,
        created_at=log.created_at,
    )
