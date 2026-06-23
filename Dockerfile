FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY streamlit_app.py /app/streamlit_app.py

RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir ".[ui]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
