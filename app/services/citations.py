from app.schemas.api import Citation
from app.services.retrieval import RankedChunk, make_quote


def build_citations(query: str, evidence: list[RankedChunk]) -> list[Citation]:
    # 从最终证据列表生成 citation，并让 citation_id 从 1 开始，方便答案中引用 [1]、[2]。
    citations = []
    for index, item in enumerate(evidence, start=1):
        citations.append(
            Citation(
                citation_id=index,
                chunk_id=item.chunk.id,
                document_id=item.chunk.document_id,
                source_filename=item.chunk.source_filename,
                page_number=item.chunk.page_number,
                section_title=item.chunk.section_title,
                quote=make_quote(query, item.chunk.content),
            )
        )
    return validate_citations(citations, evidence)


def validate_citations(citations: list[Citation], evidence: list[RankedChunk]) -> list[Citation]:
    # citation 必须引用本次检索得到的 chunk，且 quote 必须真实出现在 chunk.content 中。
    # 这一步可以防止生成阶段引用不存在的证据。
    chunks = {item.chunk.id: item.chunk for item in evidence}
    valid = []
    for citation in citations:
        chunk = chunks.get(citation.chunk_id)
        if chunk is None:
            continue
        if citation.quote and citation.quote in chunk.content:
            valid.append(citation)
    return valid
