from dataclasses import dataclass
from typing import Any

import requests

from app.core.config import get_settings
from app.core.errors import AppError


@dataclass(frozen=True)
class RerankHit:
    index: int
    score: float


class BaseReranker:
    provider: str
    model: str

    def rerank(
        self,
        query: str,
        documents: list[str],
        base_scores: list[float],
    ) -> list[RerankHit]:
        raise NotImplementedError


class LexicalReranker(BaseReranker):
    provider = "lexical"
    model = "lexical-overlap"

    def rerank(
        self,
        query: str,
        documents: list[str],
        base_scores: list[float],
    ) -> list[RerankHit]:
        hits = []
        for index, document in enumerate(documents):
            base_score = base_scores[index] if index < len(base_scores) else 0.0
            hits.append(
                RerankHit(
                    index=index,
                    score=base_score + lexical_overlap_score(query, document),
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits


class DashScopeReranker(BaseReranker):
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        endpoint_path: str,
        timeout_seconds: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.endpoint_path = endpoint_path
        self.timeout_seconds = timeout_seconds

    def rerank(
        self,
        query: str,
        documents: list[str],
        base_scores: list[float],
    ) -> list[RerankHit]:
        if not documents:
            return []
        self._validate_config()
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        try:
            response = requests.post(
                self._url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AppError(
                502,
                "rerank_request_failed",
                "Rerank API request failed.",
                {"error": str(exc)},
            ) from exc

        if response.status_code >= 400:
            raise AppError(
                502,
                "rerank_http_error",
                f"Rerank API returned HTTP {response.status_code}.",
                {"status_code": response.status_code},
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AppError(
                502,
                "rerank_response_invalid",
                "Rerank API response was not valid JSON.",
            ) from exc

        if isinstance(data, dict) and data.get("code") and not _extract_raw_results(data):
            raise AppError(
                502,
                "rerank_api_error",
                "Rerank API returned an error response.",
                {"external_code": str(data.get("code")), "message": str(data.get("message", ""))},
            )

        return _parse_hits(data, len(documents), base_scores)

    def _validate_config(self) -> None:
        if not self.model:
            raise AppError(500, "rerank_model_missing", "RAG_RERANK_MODEL is required.")
        if not self.api_key:
            raise AppError(
                500,
                "rerank_api_key_missing",
                "RAG_RERANK_API_KEY or a reusable provider API key is required.",
            )

    def _url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.endpoint_path.lstrip('/')}"


def get_reranker() -> BaseReranker:
    settings = get_settings()
    provider = settings.rerank_provider.strip().lower()
    if provider in {"", "lexical", "mock"}:
        return LexicalReranker()
    if provider in {"dashscope", "openai-compatible"}:
        api_key = settings.rerank_api_key or settings.dashscope_api_key or settings.openai_api_key
        return DashScopeReranker(
            provider=provider,
            model=settings.rerank_model,
            api_key=api_key,
            base_url=settings.rerank_base_url,
            endpoint_path=settings.rerank_endpoint_path,
            timeout_seconds=settings.rerank_timeout_seconds,
        )
    raise AppError(500, "rerank_provider_unsupported", "Unsupported rerank provider.")


def lexical_overlap_score(query: str, content: str) -> float:
    query_terms = {term.lower() for term in query.split() if term.strip()}
    if not query_terms:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for term in query_terms if term in content_lower)
    return hits / len(query_terms)


def _parse_hits(data: Any, document_count: int, base_scores: list[float]) -> list[RerankHit]:
    raw_results = _extract_raw_results(data)
    if not isinstance(raw_results, list):
        raise AppError(
            502,
            "rerank_response_invalid",
            "Rerank API response did not include a results list.",
        )

    hits = []
    seen = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise AppError(
                502,
                "rerank_response_invalid",
                "Rerank API result item was not an object.",
            )
        try:
            index = int(raw["index"])
            score = float(raw["relevance_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                502,
                "rerank_response_invalid",
                "Rerank API result item missed index or relevance_score.",
            ) from exc
        if index < 0 or index >= document_count or index in seen:
            raise AppError(
                502,
                "rerank_response_invalid",
                "Rerank API result item included an invalid index.",
            )
        seen.add(index)
        hits.append(RerankHit(index=index, score=score))

    hits.sort(key=lambda hit: hit.score, reverse=True)
    for index in range(document_count):
        if index not in seen:
            base_score = base_scores[index] if index < len(base_scores) else 0.0
            hits.append(RerankHit(index=index, score=base_score))
    return hits


def _extract_raw_results(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("results"), list):
        return data["results"]
    output = data.get("output")
    if isinstance(output, dict) and isinstance(output.get("results"), list):
        return output["results"]
    return None
