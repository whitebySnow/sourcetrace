import argparse
import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sourcetrace.evaluation.assessment_diagnostics import (
    build_evidence_assessment_diagnostics,
)
from sourcetrace.evaluation.citation_diagnostics import build_citation_diagnostics
from sourcetrace.evaluation.dataset import (
    load_dataset,
    load_hybrid_query_plan,
    load_judgments,
    load_report,
)
from sourcetrace.evaluation.fixtures import (
    FixtureEvaluationSubject,
    load_fixture_observations,
)
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.models import EvaluationRunMetadata
from sourcetrace.evaluation.retrieval_diagnostics import build_retrieval_diagnostics
from sourcetrace.evaluation.review import apply_judgments


class _JsonArtifact(Protocol):
    def model_dump_json(self, *, indent: int) -> str: ...


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sourcetrace-eval")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    fake = subparsers.add_parser("fake")
    fake.add_argument("--dataset", type=Path, required=True)
    fake.add_argument("--observations", type=Path, required=True)
    fake.add_argument("--metadata", type=Path, required=True)
    fake.add_argument("--output", type=Path, required=True)
    real = subparsers.add_parser("real")
    real.add_argument("--dataset", type=Path, required=True)
    real.add_argument("--code-commit", required=True)
    real.add_argument("--output", type=Path, required=True)
    real.add_argument(
        "--confirm-real-provider",
        action="store_true",
        required=True,
        help="confirm that this command may use the database, embedding model, and LLM API",
    )
    review = subparsers.add_parser("review")
    review.add_argument("--report", type=Path, required=True)
    review.add_argument("--judgments", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    diagnose = subparsers.add_parser("diagnose-retrieval")
    diagnose.add_argument("--dataset", type=Path, required=True)
    diagnose.add_argument("--report", type=Path, required=True)
    diagnose.add_argument("--output", type=Path, required=True)
    diagnose_citations = subparsers.add_parser("diagnose-citations")
    diagnose_citations.add_argument("--dataset", type=Path, required=True)
    diagnose_citations.add_argument("--report", type=Path, required=True)
    diagnose_citations.add_argument("--output", type=Path, required=True)
    diagnose_assessments = subparsers.add_parser("diagnose-assessments")
    diagnose_assessments.add_argument("--dataset", type=Path, required=True)
    diagnose_assessments.add_argument("--report", type=Path, required=True)
    diagnose_assessments.add_argument("--output", type=Path, required=True)
    rerank = subparsers.add_parser("rerank")
    rerank.add_argument("--dataset", type=Path, required=True)
    rerank.add_argument("--report", type=Path, required=True)
    rerank.add_argument("--model", type=Path, required=True)
    rerank.add_argument("--model-revision", required=True)
    rerank.add_argument("--model-weight-sha256", required=True)
    rerank.add_argument("--code-commit", required=True)
    rerank.add_argument("--device", default="cuda")
    rerank.add_argument("--batch-size", type=int, default=8)
    rerank.add_argument("--output", type=Path, required=True)
    rerank.add_argument(
        "--confirm-local-model",
        action="store_true",
        required=True,
        help="confirm that this command may use the database and local reranker model",
    )
    hybrid = subparsers.add_parser("hybrid-retrieval")
    hybrid.add_argument("--dataset", type=Path, required=True)
    hybrid.add_argument("--query-plan", type=Path, required=True)
    hybrid.add_argument("--code-commit", required=True)
    hybrid.add_argument("--output", type=Path, required=True)
    hybrid.add_argument(
        "--confirm-local-model",
        action="store_true",
        required=True,
        help=(
            "confirm that this command may use PostgreSQL and local embedding and reranker models"
        ),
    )
    return parser


async def _run_fake(args: argparse.Namespace) -> None:
    dataset = load_dataset(args.dataset)
    observations = load_fixture_observations(args.observations)
    metadata = EvaluationRunMetadata.model_validate_json(args.metadata.read_text(encoding="utf-8"))
    subject = FixtureEvaluationSubject(dataset, observations)
    report = await EvaluationHarness().run(dataset, subject, metadata=metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _failure_output_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}-failure{output.suffix or '.json'}")


async def _run_real(args: argparse.Namespace) -> int:
    from sourcetrace.core.config import get_settings
    from sourcetrace.evaluation.real import RealEvaluationFailure, run_real_evaluation

    failure_output = _failure_output_path(args.output)
    if args.output.exists() or failure_output.exists():
        raise FileExistsError(
            "real evaluation output or failure artifact already exists; choose a new output path"
        )
    dataset = load_dataset(args.dataset)
    try:
        report = await run_real_evaluation(
            dataset,
            code_commit=args.code_commit,
            settings=get_settings(),
        )
    except RealEvaluationFailure as error:
        _write_json_artifact(failure_output, error.report)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0


async def _run_rerank(args: argparse.Namespace) -> None:
    from sourcetrace.evaluation.reranker_real import run_real_reranker_evaluation

    dataset_bytes = args.dataset.read_bytes()
    report_bytes = args.report.read_bytes()
    reranker_report = await run_real_reranker_evaluation(
        load_dataset(args.dataset),
        load_report(args.report),
        dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(),
        source_report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        code_commit=args.code_commit,
        model=args.model,
        model_revision=args.model_revision,
        model_weight_sha256=args.model_weight_sha256,
        device=args.device,
        batch_size=args.batch_size,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        reranker_report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


async def _run_hybrid_retrieval(args: argparse.Namespace) -> None:
    from sourcetrace.core.config import get_settings
    from sourcetrace.evaluation.hybrid_real import (
        run_real_hybrid_retrieval_evaluation,
    )

    dataset_bytes = args.dataset.read_bytes()
    query_plan_bytes = args.query_plan.read_bytes()
    report = await run_real_hybrid_retrieval_evaluation(
        load_dataset(args.dataset),
        load_hybrid_query_plan(args.query_plan),
        dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(),
        query_plan_sha256=hashlib.sha256(query_plan_bytes).hexdigest(),
        code_commit=args.code_commit,
        settings=get_settings(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _run_review(args: argparse.Namespace) -> None:
    report_bytes = args.report.read_bytes()
    report = load_report(args.report)
    reviewed = apply_judgments(
        report,
        load_judgments(args.judgments),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(reviewed.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _run_diagnose_retrieval(args: argparse.Namespace) -> None:
    report_bytes = args.report.read_bytes()
    diagnostics = build_retrieval_diagnostics(
        load_dataset(args.dataset),
        load_report(args.report),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    _write_json_artifact(args.output, diagnostics)


def _run_diagnose_citations(args: argparse.Namespace) -> None:
    report_bytes = args.report.read_bytes()
    diagnostics = build_citation_diagnostics(
        load_dataset(args.dataset),
        load_report(args.report),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    _write_json_artifact(args.output, diagnostics)


def _run_diagnose_assessments(args: argparse.Namespace) -> None:
    report_bytes = args.report.read_bytes()
    diagnostics = build_evidence_assessment_diagnostics(
        load_dataset(args.dataset),
        load_report(args.report),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
    _write_json_artifact(args.output, diagnostics)


def _write_json_artifact(output: Path, artifact: _JsonArtifact) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "fake":
        asyncio.run(_run_fake(args))
        return 0
    if args.mode == "real":
        return asyncio.run(_run_real(args))
    if args.mode == "review":
        _run_review(args)
        return 0
    if args.mode == "diagnose-retrieval":
        _run_diagnose_retrieval(args)
        return 0
    if args.mode == "diagnose-citations":
        _run_diagnose_citations(args)
        return 0
    if args.mode == "diagnose-assessments":
        _run_diagnose_assessments(args)
        return 0
    if args.mode == "rerank":
        asyncio.run(_run_rerank(args))
        return 0
    if args.mode == "hybrid-retrieval":
        asyncio.run(_run_hybrid_retrieval(args))
        return 0
    raise AssertionError("unreachable evaluation mode")


if __name__ == "__main__":
    raise SystemExit(main())
