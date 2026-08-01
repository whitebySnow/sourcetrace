import runpy
from pathlib import Path

import pytest

_SCRIPT = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "verify_mvp.py")
)


def _final_answer(*, answer: str = "The Atlas retention period is 37 days.") -> dict[str, object]:
    return {
        "type": "final",
        "answer": answer,
        "citations": [
            {
                "document_version_id": "version-1",
                "excerpt": "The Atlas retention period is exactly 37 days.",
                "source_url": "/api/v1/source",
            }
        ],
    }


def test_grounded_answer_requires_the_verification_fact_in_answer_and_excerpt() -> None:
    require_grounded_answer = _SCRIPT["_require_grounded_answer"]

    require_grounded_answer(_final_answer(), "version-1")

    with pytest.raises(RuntimeError, match="verification fact"):
        require_grounded_answer(
            _final_answer(answer="The retention period is 30 days."),
            "version-1",
        )

    invalid_excerpt = _final_answer()
    citations = invalid_excerpt["citations"]
    assert isinstance(citations, list)
    citations[0]["excerpt"] = "The retention period is configured in the policy."
    with pytest.raises(RuntimeError, match="citation excerpt"):
        require_grounded_answer(invalid_excerpt, "version-1")


def test_refusal_requires_insufficient_evidence_code() -> None:
    require_insufficient_evidence = _SCRIPT["_require_insufficient_evidence"]

    require_insufficient_evidence({"type": "refusal", "code": "INSUFFICIENT_EVIDENCE"})

    with pytest.raises(RuntimeError, match="INSUFFICIENT_EVIDENCE"):
        require_insufficient_evidence({"type": "refusal", "code": "LLM_TIMEOUT"})


def test_cancelled_history_cannot_persist_answer_or_citations() -> None:
    require_cancelled_history = _SCRIPT["_require_cancelled_history"]
    require_cancel_requested = _SCRIPT["_require_cancel_requested"]
    item = {
        "id": "run-1",
        "status": "cancelled",
        "outcome": None,
        "answer": None,
        "citations": [],
    }

    require_cancelled_history([item], "run-1")
    require_cancel_requested("cancel_requested")

    item["answer"] = "Partial answer"
    with pytest.raises(RuntimeError, match="persisted answer"):
        require_cancelled_history([item], "run-1")

    with pytest.raises(RuntimeError, match="cancel_requested"):
        require_cancel_requested("cancelled")
