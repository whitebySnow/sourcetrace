from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from uuid import UUID

import torch
from sentence_transformers import CrossEncoder

from sourcetrace.db.session import session_factory
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationReport,
    ObservedEvidence,
    ObservedFusedCandidateTrace,
    RerankedCandidateTrace,
    RerankerCaseResult,
    RerankerEvaluationReport,
    RerankerEvaluationSummary,
    RerankerRunMetadata,
)
from sourcetrace.evaluation.repository import EvaluationCorpusRepository
from sourcetrace.evaluation.reranking import (
    RerankableCandidate,
    Reranker,
    rerank_fixed_candidates,
)
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository


class CrossEncoderReranker(Reranker):
    def __init__(self, model: Path, *, device: str, batch_size: int) -> None:
        if batch_size <= 0:
            raise ValueError("reranker batch size must be positive")
        self._model = CrossEncoder(str(model), device=device)
        self._batch_size = batch_size

    def score(self, question: str, passages: Sequence[str]) -> tuple[float, ...]:
        scores = self._model.predict(
            [(question, passage) for passage in passages],
            batch_size=self._batch_size,
            show_progress_bar=False,
        )
        return tuple(float(score) for score in scores)


async def run_real_reranker_evaluation(
    dataset: EvaluationDataset,
    baseline: EvaluationReport,
    *,
    dataset_sha256: str,
    source_report_sha256: str,
    code_commit: str,
    model: Path,
    model_revision: str,
    model_weight_sha256: str,
    device: str,
    batch_size: int,
) -> RerankerEvaluationReport:
    _validate_snapshot(dataset, baseline)
    weight_path = model / "model.safetensors"
    actual_weight_sha256 = _sha256(weight_path)
    if actual_weight_sha256 != model_weight_sha256.lower():
        raise ValueError("reranker model weight SHA-256 does not match the requested revision")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA reranker requested but CUDA is unavailable")

    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    load_started = perf_counter()
    reranker = CrossEncoderReranker(model, device=device, batch_size=batch_size)
    model_load_ms = (perf_counter() - load_started) * 1000

    case_by_id = {case.id: case for case in dataset.cases}
    fused_by_case: dict[str, tuple[ObservedFusedCandidateTrace, ...]] = {}
    all_chunk_ids: set[UUID] = set()
    for result in baseline.cases:
        trace = result.observation.decision_trace
        fused: tuple[ObservedFusedCandidateTrace, ...]
        if trace is None or not trace.retrieval_rounds:
            fused = ()
        else:
            fused = trace.retrieval_rounds[-1].fused_candidates
        fused_by_case[result.case_id] = fused
        all_chunk_ids.update(item.chunk_id for item in fused)

    results: list[RerankerCaseResult] = []
    total_rerank_ms = 0.0
    async with session_factory() as session:
        chunks = await EvaluationCorpusRepository(session).get_chunks(
            dataset.knowledge_base_id,
            dataset.document_version_ids,
            tuple(all_chunk_ids),
        )
        retrieval_repository = PgVectorRetrievalRepository(
            session,
            document_version_ids=dataset.document_version_ids,
        )
        for baseline_result in baseline.cases:
            case = case_by_id[baseline_result.case_id]
            fused = fused_by_case[case.id]
            candidates = tuple(
                RerankableCandidate(
                    evidence=replace(
                        chunks[item.chunk_id],
                        score=item.best_raw_cosine_score,
                    ),
                    fused_score=item.fused_score,
                    best_raw_score=item.best_raw_cosine_score,
                    baseline_rank=rank,
                    baseline_selected=item.selected_as_primary,
                )
                for rank, item in enumerate(fused, start=1)
            )
            rerank_started = perf_counter()
            reranked = rerank_fixed_candidates(
                case.question,
                candidates,
                reranker=reranker,
                limit=baseline.metadata.retrieval_top_k,
            )
            rerank_ms = (perf_counter() - rerank_started) * 1000
            total_rerank_ms += rerank_ms
            expanded = list(reranked.primary_evidence)
            if reranked.primary_evidence and baseline.metadata.retrieval_page_neighbor_count > 0:
                expanded.extend(
                    await retrieval_repository.expand_page_neighbors(
                        dataset.knowledge_base_id,
                        reranked.primary_evidence,
                        neighbor_count=baseline.metadata.retrieval_page_neighbor_count,
                    )
                )
            observed = tuple(
                ObservedEvidence(
                    document_version_id=item.document_version_id,
                    page_number=item.page_number,
                    text=item.text,
                )
                for item in expanded
            )
            reranked_status = EvaluationHarness.retrieval_status(
                case.expected.evidence,
                observed,
            )
            results.append(
                RerankerCaseResult(
                    case_id=case.id,
                    baseline_retrieval=baseline_result.retrieval,
                    reranked_retrieval=reranked_status,
                    rerank_ms=rerank_ms,
                    candidates=tuple(
                        RerankedCandidateTrace(
                            chunk_id=item.evidence.chunk_id,
                            document_version_id=item.evidence.document_version_id,
                            page_number=item.evidence.page_number,
                            baseline_rank=item.baseline_rank,
                            reranked_rank=item.reranked_rank,
                            baseline_selected=item.baseline_selected,
                            reranked_selected=item.selected_as_primary,
                            fused_score=item.fused_score,
                            best_raw_cosine_score=item.best_raw_score,
                            reranker_score=item.reranker_score,
                        )
                        for item in reranked.candidates
                    ),
                    selected_primary_chunk_ids=tuple(
                        item.chunk_id for item in reranked.primary_evidence
                    ),
                    expanded_evidence_chunk_ids=tuple(item.chunk_id for item in expanded),
                )
            )

    if device.startswith("cuda"):
        torch.cuda.synchronize()
        peak_vram_mib = torch.cuda.max_memory_allocated() / 1024 / 1024
        device_name = torch.cuda.get_device_name(torch.cuda.current_device())
    else:
        peak_vram_mib = None
        device_name = "CPU"
    improvements = tuple(
        item.case_id
        for item in results
        if item.baseline_retrieval == "failed" and item.reranked_retrieval == "passed"
    )
    regressions = tuple(
        item.case_id
        for item in results
        if item.baseline_retrieval == "passed" and item.reranked_retrieval == "failed"
    )
    return RerankerEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=dataset.document_version_ids,
        metadata=RerankerRunMetadata(
            code_commit=code_commit,
            source_report_sha256=source_report_sha256.lower(),
            dataset_sha256=dataset_sha256.lower(),
            model_name=_model_identity(model),
            model_revision=model_revision,
            model_weight_sha256=actual_weight_sha256,
            device=device,
            device_name=device_name,
            torch_version=str(torch.__version__),
            batch_size=batch_size,
            model_load_ms=model_load_ms,
            total_rerank_ms=total_rerank_ms,
            peak_vram_mib=peak_vram_mib,
        ),
        cases=tuple(results),
        summary=RerankerEvaluationSummary(
            baseline_passed=sum(item.baseline_retrieval == "passed" for item in results),
            reranked_passed=sum(item.reranked_retrieval == "passed" for item in results),
            not_applicable=sum(item.reranked_retrieval == "not_applicable" for item in results),
            improvements=improvements,
            regressions=regressions,
        ),
    )


def _validate_snapshot(dataset: EvaluationDataset, baseline: EvaluationReport) -> None:
    if (dataset.dataset_id, dataset.dataset_version) != (
        baseline.dataset_id,
        baseline.dataset_version,
    ):
        raise ValueError("baseline report does not belong to the supplied dataset")
    if dataset.knowledge_base_id != baseline.knowledge_base_id:
        raise ValueError("baseline report knowledge base does not match the dataset")
    if dataset.document_version_ids != baseline.document_version_ids:
        raise ValueError("baseline report document snapshot does not match the dataset")
    if {case.id for case in dataset.cases} != {case.case_id for case in baseline.cases}:
        raise ValueError("baseline report cases do not match the dataset")


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _model_identity(model: Path) -> str:
    parts = model.resolve().parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else model.name
