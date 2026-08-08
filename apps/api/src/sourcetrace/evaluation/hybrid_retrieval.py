from __future__ import annotations

from collections.abc import Mapping

from sourcetrace.evaluation.models import HybridQueryPlanFixture


def resolve_query_plans(
    questions: Mapping[str, str],
    fixture: HybridQueryPlanFixture,
    *,
    dataset_id: str,
    dataset_version: str,
) -> dict[str, tuple[str, ...]]:
    if (fixture.dataset_id, fixture.dataset_version) != (dataset_id, dataset_version):
        raise ValueError("query plan fixture does not belong to the supplied dataset")
    overrides: dict[str, tuple[str, ...]] = {}
    for item in fixture.cases:
        if item.case_id not in questions:
            raise ValueError("query plan fixture contains an unknown case")
        if item.case_id in overrides:
            raise ValueError("query plan fixture contains a duplicate case")
        additional = tuple(query.strip() for query in item.additional_queries)
        if any(not query for query in additional) or len(set(additional)) != len(additional):
            raise ValueError("query plan fixture contains invalid additional queries")
        overrides[item.case_id] = additional
    return {
        case_id: (question, *overrides.get(case_id, ()))
        for case_id, question in questions.items()
    }
