import time
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Conversation, Message
from app.schemas.api import ChatResponse
from app.services.citations import build_citations
from app.services.json_utils import dumps_json
from app.services.knowledge_bases import get_knowledge_base_or_404
from app.services.retrieval import RankedChunk, retrieve


def chat_with_knowledge_base(
    db: Session,
    knowledge_base_id: str,
    query: str,
    top_k: int,
    retrieval_mode: str,
    conversation_id: Optional[str] = None,
) -> ChatResponse:
    # 聊天主链路：会话管理 -> 检索证据 -> citation 校验 -> 生成答案 -> 持久化消息和日志。
    get_knowledge_base_or_404(db, knowledge_base_id)
    started = time.perf_counter()
    conversation = get_or_create_conversation(db, knowledge_base_id, query, conversation_id)
    retrieval = retrieve(
        db=db,
        knowledge_base_id=knowledge_base_id,
        query=query,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        conversation_id=conversation.id,
    )

    generation_started = time.perf_counter()
    # citations 既用于返回给用户，也作为生成答案时要求模型引用的证据清单。
    citations = build_citations(query, retrieval.results)
    refusal = len(citations) == 0
    if refusal:
        answer = (
            "I do not have enough evidence in the selected knowledge base to answer this "
            "question. Add or reindex relevant documents, then try again."
        )
    else:
        answer = generate_answer(
            query, retrieval.results, [item.model_dump() for item in citations]
        )
    generation_ms = int((time.perf_counter() - generation_started) * 1000)

    settings = get_settings()
    # 同一次 chat 会保存一条 user message 和一条 assistant message，方便后续读取历史。
    db.add(Message(conversation_id=conversation.id, role="user", content=query))
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        citations_json=dumps_json([item.model_dump() for item in citations]),
        retrieval_log_id=retrieval.log.id,
        model_name=settings.chat_model,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.add(assistant_message)
    retrieval.log.conversation_id = conversation.id
    retrieval.log.generation_latency_ms = generation_ms
    retrieval.log.total_latency_ms = int((time.perf_counter() - started) * 1000)
    retrieval.log.model_name = settings.chat_model
    db.commit()

    return ChatResponse(
        answer=answer,
        citations=citations,
        conversation_id=conversation.id,
        retrieval_log_id=retrieval.log.id,
        refusal=refusal,
        model_name=settings.chat_model,
    )


def get_or_create_conversation(
    db: Session,
    knowledge_base_id: str,
    query: str,
    conversation_id: Optional[str],
) -> Conversation:
    # 如果前端传入 conversation_id，就把新消息追加到现有会话；否则用问题前 80 字建标题。
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.knowledge_base_id != knowledge_base_id:
            raise AppError(404, "conversation_not_found", "Conversation not found.")
        return conversation

    title = query.strip().replace("\n", " ")[:80] or "New conversation"
    conversation = Conversation(knowledge_base_id=knowledge_base_id, title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def generate_answer(
    query: str, evidence: list[RankedChunk], citations: list[dict[str, object]]
) -> str:
    # mock 生成器不调用 LLM，只把证据列出来，适合开发和测试验证检索链路。
    settings = get_settings()
    if settings.generation_provider == "mock":
        lines = ["Based on the retrieved evidence:"]
        for citation, _item in zip(citations, evidence):
            quote = citation["quote"]
            citation_id = citation["citation_id"]
            lines.append(f"- {quote} [{citation_id}]")
        return "\n".join(lines)
    if settings.generation_provider in {"openai", "openai-compatible"}:
        return generate_openai_answer(query, evidence, citations)
    raise AppError(500, "generation_provider_unsupported", "Unsupported generation provider.")


def generate_openai_answer(
    query: str, evidence: list[RankedChunk], citations: list[dict[str, object]]
) -> str:
    # OpenAI-compatible 生成：把经过校验的证据放进 prompt，并要求模型只基于证据回答。
    settings = get_settings()
    if not settings.openai_api_key:
        raise AppError(500, "openai_api_key_missing", "RAG_OPENAI_API_KEY is required.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AppError(
            500, "openai_package_missing", "Install openai to use this provider."
        ) from exc

    context = []
    for citation, item in zip(citations, evidence):
        # 每条证据带 citation_id、文件名、页码和章节，方便模型生成可追溯答案。
        context.append(
            f"[{citation['citation_id']}] "
            f"{item.chunk.source_filename} "
            f"page={item.chunk.page_number or 'n/a'} "
            f"section={item.chunk.section_title or 'n/a'}\n"
            f"{citation['quote']}"
        )

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer only from the provided evidence. If evidence is insufficient, "
                    "refuse. Use bracket citation ids that are present in the evidence."
                ),
            },
            {"role": "user", "content": f"Question: {query}\n\nEvidence:\n" + "\n\n".join(context)},
        ],
        temperature=0,
    )
    return response.choices[0].message.content or ""
