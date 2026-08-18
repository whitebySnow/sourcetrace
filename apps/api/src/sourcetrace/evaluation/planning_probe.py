"""Bounded, non-scoreable observations for real query-planning diagnosis."""

from collections.abc import Sequence

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunMetadata,
    ObservedPlanningSlotTrace,
    ObservedPlanningTrace,
    PlanningProbeObservation,
    PlanningProbeReport,
)
from sourcetrace.rag.ports import (
    QueryPlanningFailure,
    QueryPlanningTrace,
    QuestionPlanner,
)

_MAX_CASES = 2


async def run_planning_probe(
    dataset: EvaluationDataset,
    *,
    case_ids: Sequence[str],
    planner: QuestionPlanner,
    document_titles: Sequence[str],
    metadata: EvaluationRunMetadata,
) -> PlanningProbeReport:
    """Run the production planner for at most two reviewed Dataset questions.

    The returned artifact deliberately excludes every query, prompt, title list,
    answer, expected result, and evidence passage.
    """

    cases = _select_cases(dataset, case_ids)
    observations: list[PlanningProbeObservation] = []
    for case in cases:
        try:
            proposal = await planner.plan(
                question=case.question,
                recent_questions=(),
                document_titles=document_titles,
            )
        except QueryPlanningFailure as error:
            observations.append(
                PlanningProbeObservation(
                    case_id=case.id,
                    status="failed",
                    planning=_to_observed_trace(error.planning_trace),
                    error_code=error.code,
                    error_reason=error.reason,
                )
            )
            continue
        if proposal.planning_trace is None:
            observations.append(
                PlanningProbeObservation(
                    case_id=case.id,
                    status="failed",
                    planning=_failed_trace(),
                    error_code="PLANNING_TRACE_MISSING",
                    error_reason="planning_trace_missing",
                )
            )
            continue
        observations.append(
            PlanningProbeObservation(
                case_id=case.id,
                status="planned",
                planning=_to_observed_trace(proposal.planning_trace),
            )
        )
    return PlanningProbeReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=dataset.document_version_ids,
        metadata=metadata,
        observations=tuple(observations),
    )


def _select_cases(
    dataset: EvaluationDataset, case_ids: Sequence[str]
) -> tuple[EvaluationCase, ...]:
    requested = tuple(case_ids)
    if not 1 <= len(requested) <= _MAX_CASES:
        raise ValueError("planning probes require one or two explicit case IDs")
    if len(requested) != len(set(requested)):
        raise ValueError("planning probe case IDs must be unique")
    if dataset.review.status != "reviewed":
        raise ValueError("real planning probes require a human-reviewed dataset")
    cases_by_id = {case.id: case for case in dataset.cases}
    missing = sorted(case_id for case_id in requested if case_id not in cases_by_id)
    if missing:
        raise ValueError(f"unknown planning probe case IDs: {', '.join(missing)}")
    return tuple(cases_by_id[case_id] for case_id in requested)


def _to_observed_trace(trace: QueryPlanningTrace) -> ObservedPlanningTrace:
    return ObservedPlanningTrace(
        initial_disposition=trace.initial_disposition,
        initial_correction_applied=trace.initial_correction_applied,
        initial_slot_count=trace.initial_slot_count,
        selected_slots=tuple(
            ObservedPlanningSlotTrace(
                title_anchor=slot.title_anchor,
                refinement_disposition=slot.refinement_disposition,
            )
            for slot in trace.selected_slots
        ),
    )


def _failed_trace() -> ObservedPlanningTrace:
    return ObservedPlanningTrace(
        initial_disposition="failed",
        initial_correction_applied=False,
        initial_slot_count=0,
        selected_slots=(),
    )
