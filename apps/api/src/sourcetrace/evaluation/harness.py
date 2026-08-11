from typing import Protocol

from sourcetrace.evaluation.evidence_matching import (
    evidence_status,
    match_evidence_claims,
)
from sourcetrace.evaluation.models import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationDataset,
    EvaluationObservation,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationStatus,
    EvaluationSummary,
    EvidenceReference,
    HumanJudgment,
    ObservedEvidence,
)


class EvaluationSubject(Protocol):
    async def evaluate(self, case: EvaluationCase) -> EvaluationObservation: ...


class EvaluationHarness:
    async def run(
        self,
        dataset: EvaluationDataset,
        subject: EvaluationSubject,
        *,
        metadata: EvaluationRunMetadata,
    ) -> EvaluationReport:
        results: list[CaseEvaluationResult] = []
        for case in dataset.cases:
            observation = await subject.evaluate(case)
            retrieval_matches = match_evidence_claims(
                case.expected.evidence,
                observation.retrieved_evidence,
            )
            citation_matches = match_evidence_claims(
                case.expected.evidence,
                observation.citations,
            )
            retrieval = self.retrieval_status(
                case.expected.evidence,
                observation.retrieved_evidence,
            )
            citation = self._citation_status(
                case.expected.evidence,
                observation.citations,
            )
            refusal: EvaluationStatus = "not_applicable"
            if case.expected.outcome == "refused":
                refusal = "passed" if observation.outcome == "refused" else "failed"
            results.append(
                CaseEvaluationResult(
                    case_id=case.id,
                    retrieval=retrieval,
                    citation=citation,
                    refusal=refusal,
                    end_to_end=self._end_to_end(
                        expected_outcome=case.expected.outcome,
                        observed_outcome=observation.outcome,
                        retrieval=retrieval,
                        citation=citation,
                        refusal=refusal,
                        judgment=None,
                    ),
                    retrieval_evidence_matches=retrieval_matches,
                    citation_evidence_matches=citation_matches,
                    observation=observation,
                )
            )
        return EvaluationReport(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            knowledge_base_id=dataset.knowledge_base_id,
            document_version_ids=dataset.document_version_ids,
            metadata=metadata,
            cases=results,
            retrieval_summary=self._summarize([item.retrieval for item in results]),
            citation_summary=self._summarize([item.citation for item in results]),
            refusal_summary=self._summarize([item.refusal for item in results]),
            end_to_end_summary=self._summarize([item.end_to_end for item in results]),
        )

    @staticmethod
    def _summarize(statuses: list[EvaluationStatus]) -> EvaluationSummary:
        return EvaluationSummary(
            passed=statuses.count("passed"),
            failed=statuses.count("failed"),
            pending_review=statuses.count("pending_review"),
            not_applicable=statuses.count("not_applicable"),
        )

    @staticmethod
    def retrieval_status(
        expected: list[EvidenceReference],
        observed: tuple[ObservedEvidence, ...],
    ) -> EvaluationStatus:
        return evidence_status(expected, observed)

    @staticmethod
    def _citation_status(
        expected: list[EvidenceReference],
        observed: tuple[ObservedEvidence, ...],
    ) -> EvaluationStatus:
        return evidence_status(expected, observed)

    @staticmethod
    def _end_to_end(
        *,
        expected_outcome: str,
        observed_outcome: str,
        retrieval: EvaluationStatus,
        citation: EvaluationStatus,
        refusal: EvaluationStatus,
        judgment: HumanJudgment | None,
    ) -> EvaluationStatus:
        if expected_outcome == "refused":
            return refusal
        if observed_outcome != "answered" or "failed" in {retrieval, citation}:
            return "failed"
        return judgment or "pending_review"
