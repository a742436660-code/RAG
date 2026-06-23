from types import SimpleNamespace

from app.core.errors import AppError
from app.services.rerankers import DashScopeReranker, RerankHit, get_reranker
from app.services.retrieval import RankedChunk, run_rerank


def make_ranked(chunk_id: str, content: str, score: float, rank: int) -> RankedChunk:
    chunk = SimpleNamespace(id=chunk_id, content=content)
    return RankedChunk(chunk=chunk, score=score, source="both", rank=rank)


def test_run_rerank_orders_by_provider_scores(monkeypatch):
    class FakeReranker:
        provider = "dashscope"
        model = "qwen3-rerank"

        def rerank(self, query, documents, base_scores):
            assert query == "target"
            assert documents == ["first", "second", "third"]
            assert base_scores == [0.3, 0.2, 0.1]
            return [RerankHit(index=2, score=0.99), RerankHit(index=0, score=0.25)]

    monkeypatch.setattr("app.services.retrieval.get_reranker", lambda: FakeReranker())
    run = run_rerank(
        "target",
        [
            make_ranked("a", "first", 0.3, 1),
            make_ranked("b", "second", 0.2, 2),
            make_ranked("c", "third", 0.1, 3),
        ],
    )

    assert [item.chunk.id for item in run.results] == ["c", "a", "b"]
    assert [item.rank for item in run.results] == [1, 2, 3]
    assert [item.score for item in run.results] == [0.99, 0.25, 0.2]
    assert run.provider == "dashscope"
    assert run.model == "qwen3-rerank"
    assert run.fallback_used is False


def test_run_rerank_falls_back_to_lexical(monkeypatch):
    class BrokenReranker:
        provider = "dashscope"
        model = "qwen3-rerank"

        def rerank(self, query, documents, base_scores):
            raise AppError(502, "rerank_http_error", "boom")

    monkeypatch.setattr("app.services.retrieval.get_reranker", lambda: BrokenReranker())
    run = run_rerank(
        "needle",
        [
            make_ranked("a", "unrelated text", 0.3, 1),
            make_ranked("b", "needle appears here", 0.2, 2),
        ],
    )

    assert [item.chunk.id for item in run.results] == ["b", "a"]
    assert run.provider == "lexical"
    assert run.model == "lexical-overlap"
    assert run.fallback_used is True
    assert run.fallback_reason == "rerank_failed:rerank_http_error"


def test_dashscope_reranker_parses_scores_and_keeps_missing_tail(monkeypatch):
    calls = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"results": [{"index": 1, "relevance_score": 0.8}]}

    def fake_post(url, headers, json, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        calls["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.rerankers.requests.post", fake_post)
    reranker = DashScopeReranker(
        provider="dashscope",
        model="qwen3-rerank",
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com",
        endpoint_path="/compatible-api/v1/reranks",
        timeout_seconds=10,
    )

    hits = reranker.rerank("query", ["a", "b", "c"], [0.3, 0.2, 0.1])

    assert [hit.index for hit in hits] == [1, 0, 2]
    assert [hit.score for hit in hits] == [0.8, 0.3, 0.1]
    assert calls["url"] == "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
    assert calls["headers"]["Authorization"] == "Bearer test-key"
    assert calls["json"] == {
        "model": "qwen3-rerank",
        "query": "query",
        "documents": ["a", "b", "c"],
        "top_n": 3,
    }
    assert calls["timeout"] == 10


def test_get_reranker_reuses_dashscope_key(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RAG_RERANK_MODEL", "qwen3-rerank")
    monkeypatch.delenv("RAG_RERANK_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "reused-key")
    get_settings.cache_clear()
    try:
        reranker = get_reranker()
        assert isinstance(reranker, DashScopeReranker)
        assert reranker.api_key == "reused-key"
    finally:
        get_settings.cache_clear()
