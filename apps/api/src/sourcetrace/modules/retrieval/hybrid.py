from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal, Protocol

RetrievalChannel = Literal["dense", "lexical"]


class ChannelEvidence(Protocol):
    @property
    def chunk_id(self) -> object: ...

    @property
    def score(self) -> float: ...


_LATIN_TERM_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")
_MINIMUM_LEXICAL_TERMS = 4


@dataclass(frozen=True, slots=True)
class RankedChannelCandidate[CandidateT: ChannelEvidence]:
    channel: RetrievalChannel
    rank: int
    channel_score: float
    evidence: CandidateT


@dataclass(frozen=True, slots=True)
class FusedChannelCandidate[CandidateT: ChannelEvidence]:
    evidence: CandidateT
    fused_score: float
    channel_fused_rank: int
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


def fuse_ranked_channels[CandidateT: ChannelEvidence](
    candidates: tuple[RankedChannelCandidate[CandidateT], ...],
    *,
    rank_constant: int,
    limit: int,
) -> tuple[FusedChannelCandidate[CandidateT], ...]:
    if rank_constant <= 0:
        raise ValueError("channel RRF rank constant must be positive")
    if limit <= 0:
        raise ValueError("hybrid candidate limit must be positive")

    grouped: dict[object, dict[RetrievalChannel, RankedChannelCandidate[CandidateT]]] = {}
    for candidate in candidates:
        if candidate.rank <= 0:
            raise ValueError("channel candidate rank must be positive")
        chunk_id = candidate.evidence.chunk_id
        by_channel = grouped.setdefault(chunk_id, {})
        current = by_channel.get(candidate.channel)
        if current is None or (candidate.rank, -candidate.channel_score) < (
            current.rank,
            -current.channel_score,
        ):
            by_channel[candidate.channel] = candidate

    fused: list[FusedChannelCandidate[CandidateT]] = []
    for by_channel in grouped.values():
        items = tuple(by_channel.values())
        evidence = max(items, key=lambda item: item.evidence.score).evidence
        fused.append(
            FusedChannelCandidate(
                evidence=evidence,
                fused_score=sum(1 / (rank_constant + item.rank) for item in items),
                channel_fused_rank=0,
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
    return tuple(
        replace(item, channel_fused_rank=rank)
        for rank, item in enumerate(fused[:limit], start=1)
    )
