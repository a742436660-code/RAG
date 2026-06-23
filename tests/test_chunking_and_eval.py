from app.services.chunking import chunk_document
from app.services.evaluation import (
    EvaluationSample,
    RelevantEvidence,
    RetrievalExample,
    evaluate_answer,
    evaluate_retrieval_results,
    hit_rate_at_k,
    load_evaluation_dataset,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    summarize_case_results,
)
from app.services.parsers import DocumentElement, ParsedDocument


def test_chunking_is_stable_and_deduplicates():
    parsed = ParsedDocument(
        elements=[
            DocumentElement(content="A" * 120, page_number=1, section_title="Intro"),
            DocumentElement(content="A" * 120, page_number=1, section_title="Intro"),
        ]
    )
    chunks = chunk_document(parsed, chunk_size=50, chunk_overlap=10)
    hashes = [chunk.content_hash for chunk in chunks]
    assert hashes == sorted(hashes, key=hashes.index)
    assert len(set(hashes)) == len(hashes)
    assert all(chunk.page_number == 1 for chunk in chunks)
    assert all(chunk.section_title == "Intro" for chunk in chunks)


def test_retrieval_metrics():
    example = RetrievalExample(
        query="policy",
        relevant_chunk_ids={"a", "c"},
        retrieved_chunk_ids=["b", "a", "d", "c"],
    )
    assert recall_at_k(example, 2) == 0.5
    assert precision_at_k(example, 2) == 0.5
    assert hit_rate_at_k(example, 1) == 0.0
    assert hit_rate_at_k(example, 2) == 1.0
    assert mrr_at_k(example, 4) == 0.5
    assert ndcg_at_k(example, 4) > 0.0


def test_load_evaluation_dataset_jsonl(tmp_path):
    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(
        (
            '{"id":"case1","query":"When?",'
            '"relevant_evidence":[{"source_filename":"a.md","quote":"answer text"}],'
            '"expected_answer_contains":["answer"],"expected_refusal":false}\n'
        ),
        encoding="utf-8",
    )

    samples = load_evaluation_dataset(dataset)

    assert len(samples) == 1
    assert samples[0].id == "case1"
    assert samples[0].relevant_evidence[0].source_filename == "a.md"
    assert samples[0].expected_answer_contains == ["answer"]


def test_rule_based_case_evaluation_and_summary():
    sample = EvaluationSample(
        id="case1",
        query="When are receipts due?",
        relevant_evidence=[
            RelevantEvidence(
                source_filename="travel.md",
                quote="within 30 calendar days",
            )
        ],
        expected_answer_contains=["30 calendar days"],
        expected_refusal=False,
    )
    results = [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_filename": "travel.md",
            "content": "Receipts are due within 30 calendar days.",
            "rank": 1,
            "score": 0.9,
        },
        {
            "chunk_id": "chunk-2",
            "document_id": "doc-2",
            "source_filename": "other.md",
            "content": "Unrelated policy text.",
            "rank": 2,
            "score": 0.1,
        },
    ]

    case = evaluate_retrieval_results(sample, results, "hybrid", 2)
    case.update(
        evaluate_answer(
            sample,
            "Submit receipts within 30 calendar days.",
            [
                {
                    "citation_id": 1,
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_filename": "travel.md",
                    "quote": "within 30 calendar days",
                }
            ],
            refusal=False,
        )
    )
    summary = summarize_case_results([case])

    assert case["recall@k"] == 1.0
    assert case["precision@k"] == 0.5
    assert case["answer_hint_hit"] is True
    assert case["refusal_correct"] is True
    assert case["citation_relevance_rate"] == 1.0
    assert summary["runs"][0]["recall@k"] == 1.0
    assert summary["runs"][0]["answer_hint_hit_rate"] == 1.0
