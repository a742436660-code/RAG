import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAG_DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("RAG_VECTOR_STORE_BACKEND", "local")
    monkeypatch.setenv("RAG_CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("RAG_GENERATION_PROVIDER", "mock")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "lexical")

    from app.core.config import get_settings
    from app.db.session import reset_database_state
    from app.services.vector_store import get_vector_store

    get_settings.cache_clear()
    reset_database_state()
    get_vector_store.cache_clear()

    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    reset_database_state()
    get_vector_store.cache_clear()
    for key in (
        "RAG_DATA_DIR",
        "RAG_DATABASE_URL",
        "RAG_VECTOR_STORE_BACKEND",
        "RAG_CELERY_TASK_ALWAYS_EAGER",
        "RAG_EMBEDDING_PROVIDER",
        "RAG_GENERATION_PROVIDER",
        "RAG_RERANK_PROVIDER",
    ):
        os.environ.pop(key, None)
