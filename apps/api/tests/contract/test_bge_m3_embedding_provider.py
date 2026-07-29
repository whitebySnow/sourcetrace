import os
from pathlib import Path
from typing import Any

import pytest
import sentence_transformers

from sourcetrace.rag.embeddings import (
    BgeM3EmbeddingProvider,
    EmbeddingConfig,
    EmbeddingProviderError,
    load_sentence_transformer,
)


class RecordingModel:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[dict[str, Any]] = []

    def get_embedding_dimension(self) -> int:
        return 1024

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append({"texts": texts, **kwargs})
        return self._vectors


class FailingModel(RecordingModel):
    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        raise RuntimeError("private model failure")


def embedding_config(tmp_path: Path) -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="sentence-transformers",
        model="BAAI/bge-m3",
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        cache_dir=tmp_path,
        endpoint="https://hf-mirror.com",
        device="cpu",
        batch_size=2,
        dimension=1024,
        version="bge-m3-dense-v1",
    )


async def test_bge_m3_provider_returns_normalized_vectors_in_input_order(
    tmp_path: Path,
) -> None:
    first = [0.0] * 1024
    second = [0.0] * 1024
    first[0] = 1.0
    second[1] = 1.0
    model = RecordingModel([first, second])
    provider = BgeM3EmbeddingProvider(
        embedding_config(tmp_path),
        model_loader=lambda config: model,
    )

    vectors = await provider.embed(["first", "second"])

    assert vectors == [first, second]
    assert model.calls == [
        {
            "texts": ["first", "second"],
            "batch_size": 2,
            "normalize_embeddings": True,
            "show_progress_bar": False,
            "convert_to_numpy": True,
        }
    ]


async def test_bge_m3_provider_rejects_wrong_vector_dimension(tmp_path: Path) -> None:
    model = RecordingModel([[1.0, 0.0]])
    provider = BgeM3EmbeddingProvider(
        embedding_config(tmp_path),
        model_loader=lambda config: model,
    )

    with pytest.raises(EmbeddingProviderError) as error:
        await provider.embed(["text"])

    assert error.value.code == "EMBEDDING_INVALID_OUTPUT"
    assert str(error.value) == "Embedding model returned an invalid vector"


async def test_bge_m3_provider_sanitizes_model_failures(tmp_path: Path) -> None:
    provider = BgeM3EmbeddingProvider(
        embedding_config(tmp_path),
        model_loader=lambda config: FailingModel([]),
    )

    with pytest.raises(EmbeddingProviderError) as error:
        await provider.embed(["text"])

    assert error.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert str(error.value) == "Embedding model is temporarily unavailable"
    assert "private model failure" not in str(error.value)


def test_bge_m3_loader_uses_configured_mirror_cache_and_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    model = RecordingModel([])

    def fake_sentence_transformer(model_name: str, **kwargs: Any) -> RecordingModel:
        captured["model_name"] = model_name
        captured.update(kwargs)
        return model

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.setattr(
        sentence_transformers,
        "SentenceTransformer",
        fake_sentence_transformer,
    )
    config = embedding_config(tmp_path)

    loaded = load_sentence_transformer(config)

    assert loaded is model
    assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert os.environ["HF_HOME"] == str(tmp_path)
    assert captured == {
        "model_name": "BAAI/bge-m3",
        "revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "cache_folder": str(tmp_path),
        "device": "cpu",
    }
