# Local Enterprise RAG

Single-user local enterprise document RAG knowledge base. The first version avoids
registration, login, JWT, multi-user isolation, and tenant permissions. It focuses on
document ingestion, idempotent processing, SQLite FTS5, optional ChromaDB vectors,
hybrid retrieval, RRF, citation validation, retrieval logs, evaluation helpers, and
local deployment.

## Features

- FastAPI API with request IDs, structured JSON logs, unified error responses.
- SQLite via SQLAlchemy with WAL, foreign keys, and busy timeout enabled.
- Knowledge base CRUD, stats, document upload, status, retry, reindex, delete.
- TXT and Markdown parsers built in. DOCX/PDF use Docling when available, with
  python-docx and pypdf fallback.
- Celery task entrypoint with local eager mode by default for single-machine use.
- SQLite FTS5 sparse retrieval plus optional ChromaDB dense retrieval.
- Mock deterministic embeddings and mock answer generation by default. OpenAI-compatible
  generation can be enabled with environment variables.
- Streamlit demo client that only talks to the FastAPI API.
- Docker Compose services for api, worker, redis, and streamlit.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

Seed demo data:

```powershell
python -m app.cli.seed_demo
```

Run Streamlit:

```powershell
pip install -e ".[ui]"
streamlit run streamlit_app.py
```

## Optional Full Stack Dependencies

Install optional heavy dependencies when you want ChromaDB, Docling, and PaddleOCR:

```powershell
pip install -e ".[full,dev]"
```

The app uses `RAG_VECTOR_STORE_BACKEND=auto` by default. If ChromaDB is installed it is
used as a persistent vector store. Otherwise dense retrieval falls back to a local
deterministic embedding scan over SQLite chunks so the MVP remains runnable.

## Using an Existing Embedding Key

This workspace is configured to use DashScope embeddings without copying the API key:

```env
RAG_EXTERNAL_ENV_FILE=../Langchain/.env
RAG_EMBEDDING_PROVIDER=dashscope
RAG_EMBEDDING_MODEL=text-embedding-v4
RAG_EMBEDDING_DIMENSION=1024
RAG_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

The external env file should contain `DASHSCOPE_API_KEY`.

## Using DashScope Rerank

`hybrid_rerank` uses the local lexical reranker by default. To enable DashScope
`qwen3-rerank`, configure the rerank provider and model while continuing to reuse
the existing external `DASHSCOPE_API_KEY`:

```env
RAG_RERANK_PROVIDER=dashscope
RAG_RERANK_MODEL=qwen3-rerank
RAG_RERANK_BASE_URL=https://dashscope.aliyuncs.com
RAG_RERANK_ENDPOINT_PATH=/compatible-api/v1/reranks
```

Rerank key resolution order is `RAG_RERANK_API_KEY`, then
`RAG_DASHSCOPE_API_KEY`/`DASHSCOPE_API_KEY`, then
`RAG_OPENAI_API_KEY`/`OPENAI_API_KEY`.

## Offline Evaluation

Create or update demo data first:

```powershell
python -m app.cli.seed_demo
```

Then run an offline evaluation against the printed knowledge base id:

```powershell
python -m app.cli.evaluate `
  --kb-id <knowledge_base_id> `
  --dataset data/evaluation/demo_questions.jsonl `
  --retrieval-mode hybrid `
  --retrieval-mode hybrid_rerank `
  --top-k 3 `
  --top-k 5 `
  --include-chat `
  --output output/evaluation/latest
```

The evaluator writes `summary.json`, `report.md`, and `cases.jsonl`. Retrieval
metrics show whether the expected evidence was found and ranked well; answer metrics
check expected answer hints, refusal correctness, and citation coverage. By default the
CLI only evaluates retrieval. Use `--include-chat` when you want answer and citation
metrics as well.
## Important Endpoints

- `GET /health`
- `GET /ready`
- `POST /knowledge-bases`
- `GET /knowledge-bases`
- `GET /knowledge-bases/{kb_id}/stats`
- `POST /knowledge-bases/{kb_id}/documents`
- `POST /knowledge-bases/{kb_id}/search`
- `POST /knowledge-bases/{kb_id}/chat`
- `GET /conversations/{conversation_id}`
- `GET /retrieval-logs/{log_id}`

## Checks

```powershell
pytest
ruff check .
ruff format --check .
mypy app
docker compose config
```


