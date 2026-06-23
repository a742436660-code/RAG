import argparse
import json
import time
from pathlib import Path
from typing import Optional

from app.db.init_db import init_db
from app.db.session import get_sessionmaker
from app.services.chat import generate_answer
from app.services.citations import build_citations
from app.services.evaluation import (
    evaluate_answer,
    evaluate_retrieval_results,
    generate_markdown_report,
    load_evaluation_dataset,
    summarize_case_results,
)
from app.services.json_utils import dumps_json
from app.services.retrieval import retrieve

REFUSAL_ANSWER = (
    "I do not have enough evidence in the selected knowledge base to answer this "
    "question. Add or reindex relevant documents, then try again."
)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline RAG evaluation.")
    parser.add_argument("--kb-id", required=True, help="Knowledge base id to evaluate.")
    parser.add_argument("--dataset", required=True, help="JSONL evaluation dataset path.")
    parser.add_argument(
        "--retrieval-mode",
        action="append",
        default=[],
        choices=["sparse", "dense", "hybrid", "hybrid_rerank"],
        help="Retrieval mode to evaluate. Can be passed multiple times.",
    )
    parser.add_argument(
        "--top-k",
        action="append",
        type=int,
        default=[],
        help="Top K to evaluate. Can be passed multiple times.",
    )
    parser.add_argument(
        "--include-chat",
        action="store_true",
        help="Also generate answers and evaluate answer/citation/refusal metrics.",
    )
    parser.add_argument(
        "--output",
        default="output/evaluation/latest",
        help="Output directory for summary.json, report.md, and cases.jsonl.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    retrieval_modes = args.retrieval_mode or ["hybrid_rerank"]
    top_ks = args.top_k or [8]
    if any(top_k <= 0 for top_k in top_ks):
        raise SystemExit("--top-k must be positive.")

    init_db()
    samples = load_evaluation_dataset(args.dataset)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    db = get_sessionmaker()()
    cases = []
    try:
        for retrieval_mode in retrieval_modes:
            for top_k in top_ks:
                for sample in samples:
                    started = time.perf_counter()
                    retrieval = retrieve(
                        db=db,
                        knowledge_base_id=args.kb_id,
                        query=sample.query,
                        top_k=top_k,
                        retrieval_mode=retrieval_mode,
                    )
                    total_latency_ms = int((time.perf_counter() - started) * 1000)
                    case = evaluate_retrieval_results(
                        sample=sample,
                        results=retrieval.results,
                        retrieval_mode=retrieval_mode,
                        top_k=top_k,
                        fallback_used=bool(retrieval.log.fallback_used),
                        fallback_reason=retrieval.log.fallback_reason,
                        retrieval_log_id=retrieval.log.id,
                        retrieval_latency_ms=retrieval.log.retrieval_latency_ms,
                        total_latency_ms=total_latency_ms,
                    )

                    if args.include_chat:
                        generation_started = time.perf_counter()
                        citations = build_citations(sample.query, retrieval.results)
                        refusal = len(citations) == 0
                        if refusal:
                            answer = REFUSAL_ANSWER
                        else:
                            answer = generate_answer(
                                sample.query,
                                retrieval.results,
                                [citation.model_dump() for citation in citations],
                            )
                        generation_latency_ms = int(
                            (time.perf_counter() - generation_started) * 1000
                        )
                        case.update(
                            evaluate_answer(
                                sample=sample,
                                answer=answer,
                                citations=citations,
                                refusal=refusal,
                            )
                        )
                        case["generation_latency_ms"] = generation_latency_ms
                        case["total_latency_ms"] = int((time.perf_counter() - started) * 1000)

                    cases.append(case)
    finally:
        db.close()

    summary = summarize_case_results(cases)
    summary.update(
        {
            "dataset": str(args.dataset),
            "knowledge_base_id": args.kb_id,
            "include_chat": bool(args.include_chat),
            "sample_count": len(samples),
        }
    )
    _write_outputs(output_dir, summary, cases)
    print(f"summary={output_dir / 'summary.json'}")
    print(f"report={output_dir / 'report.md'}")
    print(f"cases={output_dir / 'cases.jsonl'}")


def _write_outputs(output_dir: Path, summary: dict, cases: list[dict]) -> None:
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        generate_markdown_report(summary, cases), encoding="utf-8"
    )
    (output_dir / "cases.jsonl").write_text(
        "".join(f"{dumps_json(case)}\n" for case in cases), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
