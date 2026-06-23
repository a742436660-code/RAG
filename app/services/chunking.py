import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.parsers import ParsedDocument


@dataclass
class ChunkCandidate:
    # 写入数据库前的临时 chunk 对象，保存文本、来源信息和去重 hash。
    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    page_number: Optional[int]
    section_title: Optional[str]
    element_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_document(
    parsed: ParsedDocument,
    chunk_size: int,
    chunk_overlap: int,
) -> list[ChunkCandidate]:
    # 把 ParsedDocument 的每个元素切成适合检索和塞进 LLM 上下文的小片段。
    # 同一文档内用 content_hash 去重，避免重复页眉/页脚或重复段落污染检索结果。
    seen_hashes: set[str] = set()
    chunks: list[ChunkCandidate] = []
    index = 0

    for element in parsed.elements:
        content = normalize_content(element.content)
        if not content:
            continue
        for text_part in split_text(content, chunk_size=chunk_size, overlap=chunk_overlap):
            content_hash = hashlib.sha256(text_part.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            chunks.append(
                ChunkCandidate(
                    chunk_index=index,
                    content=text_part,
                    content_hash=content_hash,
                    token_count=estimate_tokens(text_part),
                    page_number=element.page_number,
                    section_title=element.section_title,
                    element_type=element.element_type,
                    metadata=element.metadata,
                )
            )
            index += 1
    return chunks


def normalize_content(content: str) -> str:
    # 规范化空白字符，减少同一文本因换行/空格差异产生不同 hash。
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def split_text(content: str, chunk_size: int, overlap: int) -> list[str]:
    # 按字符长度切分，并尽量在换行或句号处截断，降低语义被硬切开的概率。
    # overlap 让相邻 chunk 保留上下文，避免答案刚好落在边界时丢失信息。
    if len(content) <= chunk_size:
        return [content]
    parts = []
    start = 0
    safe_overlap = min(max(overlap, 0), chunk_size - 1)
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            boundary = max(content.rfind("\n", start, end), content.rfind("。", start, end))
            boundary = max(boundary, content.rfind(".", start, end))
            if boundary > start + int(chunk_size * 0.5):
                end = boundary + 1
        part = content[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(content):
            break
        start = max(end - safe_overlap, start + 1)
    return parts


def estimate_tokens(text: str) -> int:
    # 轻量 token 估算：英文按单词数，非空无词文本按字符数粗略折半。
    # 这里不引入 tokenizer，保持本地 MVP 依赖简单。
    words = re.findall(r"\w+", text)
    if words:
        return len(words)
    return max(1, len(text) // 2)


def metadata_to_json(metadata: dict[str, Any]) -> str:
    # 元数据统一压缩成 JSON 字符串存库，避免 schema 频繁变化。
    return json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
