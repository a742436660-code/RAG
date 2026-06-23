import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


@dataclass
class RetrievalExample:
    query: str
    relevant_chunk_ids: set[str]
    retrieved_chunk_ids: list[str]


@dataclass(frozen=True)
class RelevantEvidence:
    quote: str
    source_filename: Optional[str] = None


@dataclass(frozen=True)
class EvaluationSample:
    id: str
    query: str
    relevant_evidence: list[RelevantEvidence]
    expected_answer_contains: list[str]
    expected_refusal: bool = False


def recall_at_k(example: RetrievalExample, k: int) -> float:
    if not example.relevant_chunk_ids:
        return 0.0
    retrieved = set(example.retrieved_chunk_ids[:k])
    return len(retrieved & example.relevant_chunk_ids) / len(example.relevant_chunk_ids)


def precision_at_k(example: RetrievalExample, k: int) -> float:
    if k <= 0:
        return 0.0
    retrieved = example.retrieved_chunk_ids[:k]
    if not retrieved:
        return 0.0
    return len(set(retrieved) & example.relevant_chunk_ids) / min(k, len(retrieved))


def hit_rate_at_k(example: RetrievalExample, k: int) -> float:
    return 1.0 if set(example.retrieved_chunk_ids[:k]) & example.relevant_chunk_ids else 0.0


def mrr_at_k(example: RetrievalExample, k: int) -> float:
    for index, chunk_id in enumerate(example.retrieved_chunk_ids[:k], start=1):
        if chunk_id in example.relevant_chunk_ids:
            return 1.0 / index
    return 0.0


def ndcg_at_k(example: RetrievalExample, k: int) -> float:
    dcg = 0.0
    for index, chunk_id in enumerate(example.retrieved_chunk_ids[:k], start=1):
        relevance = 1.0 if chunk_id in example.relevant_chunk_ids else 0.0
        dcg += relevance / math.log2(index + 1)
    ideal_hits = min(len(example.relevant_chunk_ids), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def citation_validity_rate(total_citations: int, valid_citations: int) -> float:
    if total_citations <= 0:
        return 1.0
    return valid_citations / total_citations


def refusal_accuracy(expected_refusal: list[bool], actual_refusal: list[bool]) -> float:
    if not expected_refusal:
        return 0.0
    correct = sum(
        1 for expected, actual in zip(expected_refusal, actual_refusal) if expected == actual
    )
    return correct / len(expected_refusal)


def load_evaluation_dataset(path: Union[str, Path]) -> list[EvaluationSample]:
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise ValueError(f"Evaluation dataset not found: {dataset_path}")
    text = dataset_path.read_text(encoding="utf-8-sig")
    if dataset_path.suffix.lower() == ".json":
        raw_items = json.loads(text or "[]")
        if not isinstance(raw_items, list):
            raise ValueError("JSON evaluation dataset must be a list.")
        return [
            _sample_from_dict(item, f"{dataset_path}:{index + 1}")
            for index, item in enumerate(raw_items)
        ]

    samples = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw_item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {dataset_path}:{line_no}") from exc
        samples.append(_sample_from_dict(raw_item, f"{dataset_path}:{line_no}"))
    return samples


def evaluate_retrieval_results(
    sample: EvaluationSample,
    results: list[Any],
    retrieval_mode: str,
    top_k: int,
    fallback_used: bool = False,
    fallback_reason: Optional[str] = None,
    retrieval_log_id: Optional[str] = None,
    retrieval_latency_ms: Optional[int] = None,
    total_latency_ms: Optional[int] = None,
) -> dict[str, Any]:
    limited_results = list(results)[:top_k]
    example, matched_indexes, retrieved_items = _build_retrieval_example(sample, limited_results)
    if sample.relevant_evidence:
        metrics: dict[str, Optional[float]] = {
            "recall@k": recall_at_k(example, top_k),
            "precision@k": precision_at_k(example, top_k),
            "hit_rate@k": hit_rate_at_k(example, top_k),
            "mrr@k": mrr_at_k(example, top_k),
            "ndcg@k": ndcg_at_k(example, top_k),
        }
    else:
        metrics = {
            name: None for name in ("recall@k", "precision@k", "hit_rate@k", "mrr@k", "ndcg@k")
        }

    return {
        "sample_id": sample.id,
        "query": sample.query,
        "retrieval_mode": retrieval_mode,
        "top_k": top_k,
        "retrieval_log_id": retrieval_log_id,
        "retrieval_latency_ms": retrieval_latency_ms,
        "total_latency_ms": total_latency_ms,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "relevant_evidence_count": len(sample.relevant_evidence),
        "matched_evidence_count": len(matched_indexes),
        "matched_evidence_indexes": sorted(matched_indexes),
        "retrieved_chunk_ids": [_string_field(item, "chunk_id") for item in limited_results],
        "retrieved": retrieved_items,
        **metrics,
    }


def evaluate_answer(
    sample: EvaluationSample,
    answer: str,
    citations: list[Any],
    refusal: bool,
) -> dict[str, Any]:
    answer_hint_hit = answer_contains_expected_hint(answer, sample.expected_answer_contains)
    citation_items = [_citation_to_dict(citation) for citation in citations]
    valid_citations = sum(
        1 for citation in citation_items if citation.get("chunk_id") and citation.get("quote")
    )
    citation_matched_indexes = _matched_evidence_indexes_for_items(sample, citation_items)
    citation_relevance = (
        len(citation_matched_indexes) / len(sample.relevant_evidence)
        if sample.relevant_evidence
        else None
    )
    return {
        "answer": answer,
        "refusal": refusal,
        "expected_refusal": sample.expected_refusal,
        "refusal_correct": sample.expected_refusal == refusal,
        "expected_answer_contains": sample.expected_answer_contains,
        "answer_hint_hit": answer_hint_hit,
        "citations": citation_items,
        "citation_validity_rate": citation_validity_rate(len(citation_items), valid_citations),
        "citation_relevance_rate": citation_relevance,
        "citation_matched_evidence_indexes": sorted(citation_matched_indexes),
    }


def answer_contains_expected_hint(answer: str, expected_hints: list[str]) -> Optional[bool]:
    hints = [hint.strip().lower() for hint in expected_hints if hint.strip()]
    if not hints:
        return None
    answer_lower = answer.lower()
    return any(hint in answer_lower for hint in hints)


def summarize_case_results(cases: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault((str(case["retrieval_mode"]), int(case["top_k"])), []).append(case)
    runs = []
    for (retrieval_mode, top_k), group_cases in sorted(groups.items()):
        runs.append(
            {
                "retrieval_mode": retrieval_mode,
                "top_k": top_k,
                "case_count": len(group_cases),
                "retrieval_case_count": sum(
                    1 for case in group_cases if case.get("relevant_evidence_count", 0) > 0
                ),
                "recall@k": _mean(case.get("recall@k") for case in group_cases),
                "precision@k": _mean(case.get("precision@k") for case in group_cases),
                "hit_rate@k": _mean(case.get("hit_rate@k") for case in group_cases),
                "mrr@k": _mean(case.get("mrr@k") for case in group_cases),
                "ndcg@k": _mean(case.get("ndcg@k") for case in group_cases),
                "answer_hint_hit_rate": _mean(case.get("answer_hint_hit") for case in group_cases),
                "refusal_accuracy": _mean(case.get("refusal_correct") for case in group_cases),
                "citation_validity_rate": _mean(
                    case.get("citation_validity_rate") for case in group_cases
                ),
                "citation_relevance_rate": _mean(
                    case.get("citation_relevance_rate") for case in group_cases
                ),
                "avg_retrieval_latency_ms": _mean(
                    case.get("retrieval_latency_ms") for case in group_cases
                ),
                "avg_total_latency_ms": _mean(case.get("total_latency_ms") for case in group_cases),
                "fallback_rate": _mean(case.get("fallback_used") for case in group_cases),
            }
        )
    return {"runs": runs}


def generate_markdown_report(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = ["# RAG Evaluation Report", "", "## Summary", ""]
    lines.append(
        "| mode | top_k | cases | recall | precision | hit_rate | mrr | ndcg | "
        "answer_hint | refusal | citation_validity | citation_relevance | latency_ms | fallback |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: |"
    )
    for run in summary.get("runs", []):
        row = (
            "| {retrieval_mode} | {top_k} | {case_count} | {recall} | "
            "{precision} | {hit_rate} | {mrr} | {ndcg} | {answer_hint} | "
            "{refusal} | {citation_validity} | {citation_relevance} | "
            "{latency} | {fallback} |"
        ).format(
            retrieval_mode=run["retrieval_mode"],
            top_k=run["top_k"],
            case_count=run["case_count"],
            recall=_format_metric(run.get("recall@k")),
            precision=_format_metric(run.get("precision@k")),
            hit_rate=_format_metric(run.get("hit_rate@k")),
            mrr=_format_metric(run.get("mrr@k")),
            ndcg=_format_metric(run.get("ndcg@k")),
            answer_hint=_format_metric(run.get("answer_hint_hit_rate")),
            refusal=_format_metric(run.get("refusal_accuracy")),
            citation_validity=_format_metric(run.get("citation_validity_rate")),
            citation_relevance=_format_metric(run.get("citation_relevance_rate")),
            latency=_format_metric(run.get("avg_total_latency_ms"), digits=1),
            fallback=_format_metric(run.get("fallback_rate")),
        )
        lines.append(row)
    failures = [case for case in cases if _case_failure_reasons(case)]
    lines.extend(["", "## Cases Needing Review", ""])
    if not failures:
        lines.append("No failed cases found by rule-based checks.")
    else:
        for case in failures[:50]:
            reasons = ", ".join(_case_failure_reasons(case))
            lines.append(
                f"- `{case['sample_id']}` mode=`{case['retrieval_mode']}` "
                f"top_k={case['top_k']}: {reasons}"
            )
    lines.append("")
    return "\n".join(lines)


def evidence_matches_text(
    evidence: RelevantEvidence, content: str, source_filename: Optional[str] = None
) -> bool:
    if evidence.source_filename and source_filename != evidence.source_filename:
        return False
    return bool(evidence.quote.strip()) and evidence.quote.strip() in content


def _sample_from_dict(raw: Any, source: str) -> EvaluationSample:
    if not isinstance(raw, dict):
        raise ValueError(f"Evaluation sample at {source} must be an object.")
    sample_id = str(raw.get("id") or "").strip()
    query = str(raw.get("query") or "").strip()
    if not sample_id:
        raise ValueError(f"Evaluation sample at {source} must include id.")
    if not query:
        raise ValueError(f"Evaluation sample at {source} must include query.")
    relevant_evidence = _parse_relevant_evidence(raw.get("relevant_evidence", []), source)
    expected_answer_contains = raw.get("expected_answer_contains", [])
    if not expected_answer_contains and raw.get("expected_answer_hint"):
        expected_answer_contains = [raw["expected_answer_hint"]]
    if isinstance(expected_answer_contains, str):
        expected_answer_contains = [expected_answer_contains]
    if not isinstance(expected_answer_contains, list):
        raise ValueError(f"expected_answer_contains at {source} must be a list or string.")
    return EvaluationSample(
        id=sample_id,
        query=query,
        relevant_evidence=relevant_evidence,
        expected_answer_contains=[str(item) for item in expected_answer_contains if str(item)],
        expected_refusal=bool(raw.get("expected_refusal", False)),
    )


def _parse_relevant_evidence(raw_items: Any, source: str) -> list[RelevantEvidence]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise ValueError(f"relevant_evidence at {source} must be a list.")
    evidence = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"relevant_evidence[{index}] at {source} must be an object.")
        quote = str(raw.get("quote") or "").strip()
        if not quote:
            raise ValueError(f"relevant_evidence[{index}] at {source} must include quote.")
        source_filename = raw.get("source_filename")
        evidence.append(
            RelevantEvidence(
                quote=quote,
                source_filename=str(source_filename) if source_filename else None,
            )
        )
    return evidence


def _build_retrieval_example(
    sample: EvaluationSample, results: list[Any]
) -> tuple[RetrievalExample, set[int], list[dict[str, Any]]]:
    relevant_ids = {f"evidence:{index}" for index in range(len(sample.relevant_evidence))}
    seen_evidence = set()
    retrieved_labels = []
    retrieved_items = []
    for position, item in enumerate(results, start=1):
        matched_indexes = find_matching_evidence_indexes(sample, item)
        first_new_match = next(
            (index for index in matched_indexes if index not in seen_evidence), None
        )
        if first_new_match is None:
            label = f"nonrelevant:{_string_field(item, 'chunk_id')}:{position}"
        else:
            seen_evidence.add(first_new_match)
            label = f"evidence:{first_new_match}"
        retrieved_labels.append(label)
        retrieved_items.append(_retrieved_item_to_dict(item, position, matched_indexes))
    return (
        RetrievalExample(sample.query, relevant_ids, retrieved_labels),
        seen_evidence,
        retrieved_items,
    )


def find_matching_evidence_indexes(sample: EvaluationSample, item: Any) -> list[int]:
    content = _string_field(item, "content") or _string_field(item, "quote")
    source_filename = _optional_string_field(item, "source_filename")
    return [
        index
        for index, evidence in enumerate(sample.relevant_evidence)
        if evidence_matches_text(evidence, content, source_filename)
    ]


def _matched_evidence_indexes_for_items(sample: EvaluationSample, items: list[Any]) -> set[int]:
    matched = set()
    for item in items:
        matched.update(find_matching_evidence_indexes(sample, item))
    return matched


def _retrieved_item_to_dict(
    item: Any, position: int, matched_evidence_indexes: list[int]
) -> dict[str, Any]:
    return {
        "rank": _int_field(item, "rank") or position,
        "chunk_id": _string_field(item, "chunk_id"),
        "document_id": _string_field(item, "document_id"),
        "source_filename": _optional_string_field(item, "source_filename"),
        "score": _float_field(item, "score"),
        "quote": _optional_string_field(item, "quote"),
        "matched_evidence_indexes": matched_evidence_indexes,
    }


def _citation_to_dict(citation: Any) -> dict[str, Any]:
    return {
        "citation_id": _int_field(citation, "citation_id"),
        "chunk_id": _string_field(citation, "chunk_id"),
        "document_id": _string_field(citation, "document_id"),
        "source_filename": _optional_string_field(citation, "source_filename"),
        "page_number": _field(citation, "page_number"),
        "section_title": _optional_string_field(citation, "section_title"),
        "quote": _string_field(citation, "quote"),
    }


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    if hasattr(item, name):
        return getattr(item, name)
    chunk = getattr(item, "chunk", None)
    if chunk is not None:
        if name == "chunk_id":
            return getattr(chunk, "id", None)
        if hasattr(chunk, name):
            return getattr(chunk, name)
    return None


def _string_field(item: Any, name: str) -> str:
    value = _field(item, name)
    return "" if value is None else str(value)


def _optional_string_field(item: Any, name: str) -> Optional[str]:
    value = _field(item, name)
    return None if value is None else str(value)


def _int_field(item: Any, name: str) -> Optional[int]:
    value = _field(item, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_field(item: Any, name: str) -> Optional[float]:
    value = _field(item, name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Any) -> Optional[float]:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


def _format_metric(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _case_failure_reasons(case: dict[str, Any]) -> list[str]:
    reasons = []
    if (
        case.get("relevant_evidence_count", 0) > 0
        and case.get("recall@k") is not None
        and float(case["recall@k"]) < 1.0
    ):
        reasons.append(f"recall={_format_metric(case.get('recall@k'))}")
    if case.get("answer_hint_hit") is False:
        reasons.append("answer_hint_missed")
    if case.get("refusal_correct") is False:
        reasons.append("refusal_mismatch")
    if (
        case.get("citation_relevance_rate") is not None
        and float(case["citation_relevance_rate"]) < 1.0
    ):
        reasons.append(f"citation_relevance={_format_metric(case.get('citation_relevance_rate'))}")
    return reasons
