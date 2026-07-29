import argparse
import asyncio
import hashlib
from collections.abc import Sequence
from pathlib import Path

from sourcetrace.evaluation.dataset import load_dataset, load_judgments, load_report
from sourcetrace.evaluation.fixtures import (
    FixtureEvaluationSubject,
    load_fixture_observations,
)
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.models import EvaluationRunMetadata
from sourcetrace.evaluation.review import apply_judgments


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
    return parser


async def _run_fake(args: argparse.Namespace) -> None:
    dataset = load_dataset(args.dataset)
    observations = load_fixture_observations(args.observations)
    metadata = EvaluationRunMetadata.model_validate_json(args.metadata.read_text(encoding="utf-8"))
    subject = FixtureEvaluationSubject(dataset, observations)
    report = await EvaluationHarness().run(dataset, subject, metadata=metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


async def _run_real(args: argparse.Namespace) -> None:
    from sourcetrace.core.config import get_settings
    from sourcetrace.evaluation.real import run_real_evaluation

    dataset = load_dataset(args.dataset)
    report = await run_real_evaluation(
        dataset,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "fake":
        asyncio.run(_run_fake(args))
        return 0
    if args.mode == "real":
        asyncio.run(_run_real(args))
        return 0
    if args.mode == "review":
        _run_review(args)
        return 0
    raise AssertionError("unreachable evaluation mode")


if __name__ == "__main__":
    raise SystemExit(main())
