"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-16 00:00:00
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("stored_filename", sa.String(length=500), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("file_extension", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("failed_stage", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=200), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        sa.Column("processing_finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_base_id", "sha256", name="uq_documents_kb_sha256"),
    )
    op.create_index(op.f("ix_documents_knowledge_base_id"), "documents", ["knowledge_base_id"])
    op.create_index(op.f("ix_documents_sha256"), "documents", ["sha256"])
    op.create_index(op.f("ix_documents_status"), "documents", ["status"])
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_knowledge_base_id"), "conversations", ["knowledge_base_id"]
    )
    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=40), nullable=False),
        sa.Column("dense_results_json", sa.Text(), nullable=False),
        sa.Column("sparse_results_json", sa.Text(), nullable=False),
        sa.Column("fusion_results_json", sa.Text(), nullable=False),
        sa.Column("rerank_results_json", sa.Text(), nullable=False),
        sa.Column("final_evidence_json", sa.Text(), nullable=False),
        sa.Column("fallback_used", sa.Integer(), nullable=False),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("retrieval_latency_ms", sa.Integer(), nullable=True),
        sa.Column("generation_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_retrieval_logs_knowledge_base_id"), "retrieval_logs", ["knowledge_base_id"]
    )
    op.create_index(op.f("ix_retrieval_logs_request_id"), "retrieval_logs", ["request_id"])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("element_type", sa.String(length=80), nullable=False),
        sa.Column("source_filename", sa.String(length=500), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("chroma_collection_name", sa.String(length=200), nullable=True),
        sa.Column("chroma_vector_id", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_chunks_document_hash"),
    )
    op.create_index(op.f("ix_document_chunks_content_hash"), "document_chunks", ["content_hash"])
    op.create_index(op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"])
    op.create_index(
        op.f("ix_document_chunks_knowledge_base_id"), "document_chunks", ["knowledge_base_id"]
    )
    op.create_table(
        "background_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("celery_task_id", sa.String(length=200), nullable=True),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_stage", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_background_tasks_document_id"), "background_tasks", ["document_id"])
    op.create_index(op.f("ix_background_tasks_status"), "background_tasks", ["status"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False),
        sa.Column("retrieval_log_id", sa.String(length=36), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retrieval_log_id"], ["retrieval_logs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts
        USING fts5(
            chunk_id UNINDEXED,
            knowledge_base_id UNINDEXED,
            document_id UNINDEXED,
            content,
            source_filename,
            section_title,
            tokenize='unicode61'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_chunks_fts")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_background_tasks_status"), table_name="background_tasks")
    op.drop_index(op.f("ix_background_tasks_document_id"), table_name="background_tasks")
    op.drop_table("background_tasks")
    op.drop_index(op.f("ix_document_chunks_knowledge_base_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_content_hash"), table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index(op.f("ix_retrieval_logs_request_id"), table_name="retrieval_logs")
    op.drop_index(op.f("ix_retrieval_logs_knowledge_base_id"), table_name="retrieval_logs")
    op.drop_table("retrieval_logs")
    op.drop_index(op.f("ix_conversations_knowledge_base_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_documents_status"), table_name="documents")
    op.drop_index(op.f("ix_documents_sha256"), table_name="documents")
    op.drop_index(op.f("ix_documents_knowledge_base_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
