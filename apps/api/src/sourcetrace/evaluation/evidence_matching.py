from collections.abc import Sequence

from sourcetrace.evaluation.models import (
    EvaluationStatus,
    EvidenceClaimMatch,
    EvidencePassage,
    EvidenceReference,
    ObservedEvidence,
)


def match_evidence_claims(
    expected: Sequence[EvidenceReference],
    observed: Sequence[ObservedEvidence],
) -> tuple[EvidenceClaimMatch, ...]:
    matches: list[EvidenceClaimMatch] = []
    for index, reference in enumerate(expected, start=1):
        claim_id = reference.claim_id or f"evidence-{index}"
        if _contains_match(reference, observed):
            matches.append(
                EvidenceClaimMatch(
                    claim_id=claim_id,
                    match_status="canonical",
                    matched_reference=reference,
                )
            )
            continue
        alternative = next(
            (
                item
                for item in reference.approved_alternatives
                if _contains_match(item, observed)
            ),
            None,
        )
        matches.append(
            EvidenceClaimMatch(
                claim_id=claim_id,
                match_status=(
                    "approved_alternative" if alternative is not None else "not_matched"
                ),
                matched_reference=alternative,
            )
        )
    return tuple(matches)


def evidence_status(
    expected: Sequence[EvidenceReference],
    observed: Sequence[ObservedEvidence],
) -> EvaluationStatus:
    if not expected:
        return "not_applicable"
    matches = match_evidence_claims(expected, observed)
    return (
        "passed"
        if all(item.match_status != "not_matched" for item in matches)
        else "failed"
    )


def _passage_matches(reference: EvidencePassage, actual: ObservedEvidence) -> bool:
    return (
        actual.document_version_id == reference.document_version_id
        and actual.page_number == reference.page_number
        and reference.text.strip() in actual.text.strip()
    )


def _contains_match(
    reference: EvidencePassage,
    observed: Sequence[ObservedEvidence],
) -> bool:
    return any(_passage_matches(reference, actual) for actual in observed)
