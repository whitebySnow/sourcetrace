from pathlib import Path
from typing import Any

import pytest

from sourcetrace.rag.rerankers import (
    BgeCrossEncoderReranker,
    RerankerConfig,
    RerankerProviderError,
    load_cross_encoder,
)


class FakeCrossEncoder:
    def __init__(self, scores: object) -> None:
        self.scores = scores
        self.calls: list[tuple[list[tuple[str, str]], dict[str, Any]]] = []

    def predict(self, inputs: list[tuple[str, str]], **kwargs: Any) -> object:
        self.calls.append((inputs, kwargs))
        return self.scores


def _config(tmp_path: Path) -> RerankerConfig:
    return RerankerConfig(
        provider="sentence-transformers",
        model="BAAI/bge-reranker-v2-m3",
        revision="revision",
        weight_sha256="a" * 64,
        cache_dir=tmp_path,
        device="cuda",
        batch_size=8,
        version="reranker-v1",
    )


async def test_reranker_lazily_loads_once_and_reuses_model(tmp_path: Path) -> None:
    model = FakeCrossEncoder([0.2, 0.8])
    loads = 0

    def load(config: RerankerConfig) -> FakeCrossEncoder:
        nonlocal loads
        loads += 1
        return model

    reranker = BgeCrossEncoderReranker(_config(tmp_path), model_loader=load)

    first = await reranker.score(question="Question", passages=("One", "Two"))
    second = await reranker.score(question="Question", passages=("One", "Two"))

    assert first == second == (0.2, 0.8)
    assert loads == 1
    assert model.calls == [
        ([("Question", "One"), ("Question", "Two")], {"batch_size": 8, "show_progress_bar": False}),
        ([("Question", "One"), ("Question", "Two")], {"batch_size": 8, "show_progress_bar": False}),
    ]


async def test_reranker_rejects_non_finite_or_mismatched_scores(tmp_path: Path) -> None:
    reranker = BgeCrossEncoderReranker(
        _config(tmp_path),
        model_loader=lambda config: FakeCrossEncoder([float("nan")]),
    )

    with pytest.raises(RerankerProviderError) as raised:
        await reranker.score(question="Question", passages=("One", "Two"))

    assert raised.value.code == "RERANKER_INVALID_OUTPUT"


def test_cross_encoder_loader_requires_local_pinned_model(tmp_path: Path) -> None:
    with pytest.raises(RerankerProviderError) as raised:
        load_cross_encoder(_config(tmp_path))

    assert raised.value.code == "RERANKER_MODEL_NOT_FOUND"
