from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.db.models import Conversation, Message
from app.schemas.api import ConversationRead, MessageRead
from app.services.json_utils import loads_json


def list_conversations(
    db: Session, knowledge_base_id: Optional[str] = None
) -> list[ConversationRead]:
    # selectinload 预加载消息，避免遍历会话时触发 N+1 查询。
    statement = select(Conversation).options(selectinload(Conversation.messages))
    if knowledge_base_id:
        statement = statement.where(Conversation.knowledge_base_id == knowledge_base_id)
    statement = statement.order_by(Conversation.created_at.desc())
    return [conversation_to_read(item) for item in db.scalars(statement).all()]


def get_conversation(db: Session, conversation_id: str) -> ConversationRead:
    # 读取单个会话时同样带出 messages，前端可直接渲染完整对话历史。
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise AppError(404, "conversation_not_found", "Conversation not found.")
    return conversation_to_read(conversation)


def conversation_to_read(conversation: Conversation) -> ConversationRead:
    # ORM 对象转 API schema，避免在路由层散落 JSON 解析和字段组装逻辑。
    return ConversationRead(
        id=conversation.id,
        knowledge_base_id=conversation.knowledge_base_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[message_to_read(message) for message in conversation.messages],
    )


def message_to_read(message: Message) -> MessageRead:
    # citations_json 在数据库中是字符串，返回 API 时还原成 list。
    return MessageRead(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        citations=loads_json(message.citations_json, []),
        retrieval_log_id=message.retrieval_log_id,
        model_name=message.model_name,
        latency_ms=message.latency_ms,
        created_at=message.created_at,
    )
