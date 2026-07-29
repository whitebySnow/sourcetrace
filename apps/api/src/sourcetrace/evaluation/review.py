from sourcetrace.evaluation.models import (
    EvaluationJudgmentSet,
    EvaluationReport,
    EvaluationSummary,
)


def apply_judgments(
    report: EvaluationReport,
    judgments: EvaluationJudgmentSet,
    *,
    report_sha256: str,
) -> EvaluationReport:
    if judgments.report_sha256 != report_sha256:
        raise ValueError("end-to-end judgments must match the reviewed report digest")
    if (
        judgments.dataset_id != report.dataset_id
        or judgments.dataset_version != report.dataset_version
    ):
        raise ValueError("end-to-end judgments must match the report dataset version")
    if report.judgment_review is not None:
        raise ValueError("the evaluation report has already been reviewed")

    expected_ids = {case.case_id for case in report.cases if case.end_to_end == "pending_review"}
    judgment_map = judgments.as_mapping()
    if set(judgment_map) != expected_ids:
        raise ValueError("judgments must cover every pending end-to-end case exactly once")
    cases = [
        case.model_copy(
            update={
                "end_to_end": judgment_map.get(case.case_id, case.end_to_end),
            }
        )
        for case in report.cases
    ]
    statuses = [case.end_to_end for case in cases]
    return report.model_copy(
        update={
            "cases": cases,
            "judgment_review": judgments.review,
            "end_to_end_summary": EvaluationSummary(
                passed=statuses.count("passed"),
                failed=statuses.count("failed"),
                pending_review=statuses.count("pending_review"),
                not_applicable=statuses.count("not_applicable"),
            ),
        }
    )
