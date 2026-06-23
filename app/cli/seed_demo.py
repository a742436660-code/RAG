import hashlib
from pathlib import Path

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.models import BackgroundTask, Document, KnowledgeBase
from app.db.session import get_sessionmaker
from app.schemas.api import KnowledgeBaseCreate
from app.services.json_utils import dumps_json
from app.services.knowledge_bases import create_knowledge_base
from app.services.storage import create_document_from_upload
from app.workers.tasks import enqueue_document_processing

DEMO_KB_NAME = "Demo Enterprise Policies"

DEMO_DOCS = {
    "travel_policy.md": """# Travel Policy

## Booking
Employees should book economy class for domestic flights. International flights longer
than eight hours may use premium economy with manager approval.

## Reimbursement
Hotel invoices, flight itineraries, and local transportation receipts must be submitted
within 30 calendar days after the trip ends.
""",
    "expense_policy.md": """# Expense Policy

## Meal Allowance
The standard meal allowance is 120 CNY per person per day for domestic business trips.

## Approval
Any single expense above 2000 CNY requires department head approval before reimbursement.
""",
    "security_policy.txt": """Security Policy

Confidential documents must not be uploaded to public cloud storage. Removable media
must be encrypted before use. Lost devices must be reported to IT within 24 hours.
""",
}

DEMO_QUESTIONS = [
    {
        "id": "travel_receipts",
        "query": "When must travel receipts be submitted?",
        "relevant_evidence": [
            {
                "source_filename": "travel_policy.md",
                "quote": "within 30 calendar days after the trip ends",
            }
        ],
        "expected_answer_contains": ["30 calendar days"],
        "expected_refusal": False,
    },
    {
        "id": "expense_approval",
        "query": "What approval is needed for an expense above 2000 CNY?",
        "relevant_evidence": [
            {
                "source_filename": "expense_policy.md",
                "quote": "requires department head approval before reimbursement",
            }
        ],
        "expected_answer_contains": ["department head approval"],
        "expected_refusal": False,
    },
    {
        "id": "unknown_vpn",
        "query": "What is the VPN policy?",
        "relevant_evidence": [],
        "expected_answer_contains": [],
        "expected_refusal": True,
    },
]


def main() -> None:
    init_db()
    db = get_sessionmaker()()
    try:
        kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == DEMO_KB_NAME))
        if kb is None:
            kb = create_knowledge_base(db, KnowledgeBaseCreate(name=DEMO_KB_NAME))

        imported = 0
        for filename, content in DEMO_DOCS.items():
            raw = content.encode("utf-8")
            sha256 = hashlib.sha256(raw).hexdigest()
            existing = db.scalar(
                select(Document).where(
                    Document.knowledge_base_id == kb.id,
                    Document.sha256 == sha256,
                )
            )
            if existing is not None:
                continue
            document = create_document_from_upload(
                db=db,
                kb=kb,
                original_filename=filename,
                content_type="text/markdown" if filename.endswith(".md") else "text/plain",
                content=raw,
                file_size=len(raw),
                sha256=sha256,
            )
            task = BackgroundTask(
                document_id=document.id,
                task_type="seed_demo",
                status="pending",
                current_stage="pending",
            )
            db.add(task)
            db.commit()
            task_id = enqueue_document_processing(document.id)
            document.task_id = task_id
            task.celery_task_id = task_id
            db.commit()
            imported += 1

        eval_dir = Path("data") / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = eval_dir / "demo_questions.jsonl"
        dataset_path.write_text(
            "".join(f"{dumps_json(question)}\n" for question in DEMO_QUESTIONS),
            encoding="utf-8",
        )
        print(f"knowledge_base_id={kb.id}")
        print(f"imported_documents={imported}")
        print(f"demo_questions={dataset_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
