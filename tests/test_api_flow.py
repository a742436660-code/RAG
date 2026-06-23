def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_upload_search_chat_flow(client):
    kb_response = client.post("/knowledge-bases", json={"name": "Policies"})
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["id"]

    content = (
        b"# Travel Policy\n\n"
        b"Receipts must be submitted within 30 calendar days after the trip ends.\n\n"
        b"Domestic flights should be economy class."
    )
    upload_response = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        files={"file": ("travel.md", content, "text/markdown")},
    )
    assert upload_response.status_code == 201
    document = upload_response.json()
    assert document["status"] == "completed"
    assert document["chunk_count"] > 0

    search_response = client.post(
        f"/knowledge-bases/{kb_id}/search",
        json={"query": "receipts 30 calendar days", "top_k": 5, "retrieval_mode": "hybrid_rerank"},
    )
    assert search_response.status_code == 200
    search = search_response.json()
    assert search["results"]
    assert search["log_id"]

    chat_response = client.post(
        f"/knowledge-bases/{kb_id}/chat",
        json={"query": "When must receipts be submitted?", "top_k": 5},
    )
    assert chat_response.status_code == 200
    chat = chat_response.json()
    assert chat["refusal"] is False
    assert chat["citations"]
    assert "30 calendar days" in chat["answer"]


def test_duplicate_rejected_in_same_kb_allowed_in_other_kb(client):
    kb1 = client.post("/knowledge-bases", json={"name": "KB1"}).json()["id"]
    kb2 = client.post("/knowledge-bases", json={"name": "KB2"}).json()["id"]
    content = b"duplicate policy text"

    first = client.post(
        f"/knowledge-bases/{kb1}/documents",
        files={"file": ("a.txt", content, "text/plain")},
    )
    assert first.status_code == 201

    duplicate = client.post(
        f"/knowledge-bases/{kb1}/documents",
        files={"file": ("a.txt", content, "text/plain")},
    )
    assert duplicate.status_code == 409

    other_kb = client.post(
        f"/knowledge-bases/{kb2}/documents",
        files={"file": ("a.txt", content, "text/plain")},
    )
    assert other_kb.status_code == 201


def test_search_rerank_failure_logs_fallback(client, monkeypatch):
    from app.core.errors import AppError

    kb_response = client.post("/knowledge-bases", json={"name": "Fallback Policies"})
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["id"]

    upload_response = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        files={
            "file": ("fallback.md", b"Needle policy text for rerank fallback.", "text/markdown")
        },
    )
    assert upload_response.status_code == 201

    class BrokenReranker:
        provider = "dashscope"
        model = "qwen3-rerank"

        def rerank(self, query, documents, base_scores):
            raise AppError(502, "rerank_http_error", "boom")

    monkeypatch.setattr("app.services.retrieval.get_reranker", lambda: BrokenReranker())

    search_response = client.post(
        f"/knowledge-bases/{kb_id}/search",
        json={"query": "Needle policy", "top_k": 5, "retrieval_mode": "hybrid_rerank"},
    )
    assert search_response.status_code == 200
    search = search_response.json()
    assert search["results"]
    assert search["fallback_used"] is True
    assert "rerank_failed:rerank_http_error" in search["fallback_reason"]

    log_response = client.get(f"/retrieval-logs/{search['log_id']}")
    assert log_response.status_code == 200
    log = log_response.json()
    assert log["fallback_used"] is True
    assert "rerank_failed:rerank_http_error" in log["fallback_reason"]
    assert log["rerank_results"][0]["rerank_provider"] == "lexical"
    assert log["rerank_results"][0]["rerank_model"] == "lexical-overlap"
    assert log["rerank_results"][0]["rerank_fallback"] is True
