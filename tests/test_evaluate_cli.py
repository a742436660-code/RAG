import json

from app.cli.evaluate import main as evaluate_main


def test_evaluate_cli_writes_reports(client, tmp_path):
    kb_response = client.post("/knowledge-bases", json={"name": "Eval KB"})
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["id"]

    upload_response = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        files={
            "file": (
                "travel.md",
                b"Receipts must be submitted within 30 calendar days after the trip ends.",
                "text/markdown",
            )
        },
    )
    assert upload_response.status_code == 201

    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "travel_receipts",
                "query": "When must receipts be submitted?",
                "relevant_evidence": [
                    {
                        "source_filename": "travel.md",
                        "quote": "within 30 calendar days",
                    }
                ],
                "expected_answer_contains": ["30 calendar days"],
                "expected_refusal": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "evaluation"

    evaluate_main(
        [
            "--kb-id",
            kb_id,
            "--dataset",
            str(dataset),
            "--retrieval-mode",
            "hybrid",
            "--retrieval-mode",
            "hybrid_rerank",
            "--top-k",
            "1",
            "--top-k",
            "2",
            "--include-chat",
            "--output",
            str(output_dir),
        ]
    )

    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    cases_path = output_dir / "cases.jsonl"
    assert summary_path.exists()
    assert report_path.exists()
    assert cases_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()]

    assert len(summary["runs"]) == 4
    assert len(cases) == 4
    assert {run["retrieval_mode"] for run in summary["runs"]} == {"hybrid", "hybrid_rerank"}
    assert {run["top_k"] for run in summary["runs"]} == {1, 2}
    assert all(case["answer_hint_hit"] is True for case in cases)
    assert "RAG Evaluation Report" in report_path.read_text(encoding="utf-8")
