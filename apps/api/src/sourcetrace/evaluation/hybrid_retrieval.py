from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sourcetrace.evaluation.models import HybridQueryPlanFixture
from sourcetrace.modules.retrieval.service import RetrievedEvidence

RetrievalChannel = Literal["dense", "lexical"]
_LATIN_TERM_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")
_MINIMUM_LEXICAL_TERMS = 4


@dataclass(frozen=True, slots=True)
class RankedChannelCandidate:
    channel: RetrievalChannel
    rank: int
    channel_score: float
    evidence: RetrievedEvidence


@dataclass(frozen=True, slots=True)
class FusedChannelCandidate:
    evidence: RetrievedEvidence
    fused_score: float
    dense_rank: int | None
    lexical_rank: int | None
    dense_score: float | None
    lexical_score: float | None


@dataclass(frozen=True, slots=True)
class LexicalSearchQuery:
    disjunction: str
    phrase_disjunction: str


def build_lexical_search_query(query: str) -> LexicalSearchQuery | None:
    terms = tuple(_LATIN_TERM_PATTERN.findall(query))
    if len(terms) < _MINIMUM_LEXICAL_TERMS:
        return None
    unique_terms = tuple(dict.fromkeys(terms))
    phrases = tuple(
        f'"{" ".join(terms[index : index + width])}"'
        for width in range(2, 5)
        for index in range(len(terms) - width + 1)
    )
    return LexicalSearchQuery(
        disjunction=" OR ".join(unique_terms),
        phrase_disjunction=" OR ".join(phrases),
    )


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


def fuse_ranked_channels(
    candidates: tuple[RankedChannelCandidate, ...],
    *,
    rank_constant: int,
    limit: int,
) -> tuple[FusedChannelCandidate, ...]:
    if rank_constant <= 0:
        raise ValueError("channel RRF rank constant must be positive")
    if limit <= 0:
        raise ValueError("hybrid candidate limit must be positive")

    grouped: dict[object, dict[RetrievalChannel, RankedChannelCandidate]] = {}
    for candidate in candidates:
        if candidate.rank <= 0:
            raise ValueError("channel candidate rank must be positive")
        by_channel = grouped.setdefault(candidate.evidence.chunk_id, {})
        current = by_channel.get(candidate.channel)
        if current is None or (candidate.rank, -candidate.channel_score) < (
            current.rank,
            -current.channel_score,
        ):
            by_channel[candidate.channel] = candidate

    fused: list[FusedChannelCandidate] = []
    for by_channel in grouped.values():
        items = tuple(by_channel.values())
        evidence = max(items, key=lambda item: item.evidence.score).evidence
        fused.append(
            FusedChannelCandidate(
                evidence=evidence,
                fused_score=sum(1 / (rank_constant + item.rank) for item in items),
                dense_rank=(by_channel["dense"].rank if "dense" in by_channel else None),
                lexical_rank=(
                    by_channel["lexical"].rank if "lexical" in by_channel else None
                ),
                dense_score=(
                    by_channel["dense"].channel_score if "dense" in by_channel else None
                ),
                lexical_score=(
                    by_channel["lexical"].channel_score
                    if "lexical" in by_channel
                    else None
                ),
            )
        )
    fused.sort(
        key=lambda item: (
            -item.fused_score,
            -item.evidence.score,
            str(item.evidence.chunk_id),
        )
    )
    return tuple(fused[:limit])
