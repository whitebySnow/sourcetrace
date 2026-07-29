import asyncio
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast


class EmbeddingProviderError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str
    model: str
    revision: str
    cache_dir: Path
    endpoint: str | None
    device: str
    batch_size: int
    dimension: int
    version: str

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("embedding batch size must be positive")
        if self.dimension <= 0:
            raise ValueError("embedding dimension must be positive")


class SentenceEmbeddingModel(Protocol):
    def get_embedding_dimension(self) -> int | None: ...

    def encode(self, texts: list[str], **kwargs: Any) -> object: ...


ModelLoader = Callable[[EmbeddingConfig], SentenceEmbeddingModel]


def validate_embeddings(
    raw_vectors: object,
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    value = raw_vectors.tolist() if hasattr(raw_vectors, "tolist") else raw_vectors
    try:
        rows = cast(Sequence[Sequence[Any]], value)
        vectors = [[float(component) for component in row] for row in rows]
    except (TypeError, ValueError) as error:
        raise EmbeddingProviderError(
            "EMBEDDING_INVALID_OUTPUT",
            "Embedding model returned an invalid vector",
        ) from error

    invalid = len(vectors) != expected_count
    for vector in vectors:
        norm = math.sqrt(sum(component * component for component in vector))
        invalid = invalid or (
            len(vector) != dimension
            or not all(math.isfinite(component) for component in vector)
            or not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4)
        )
    if invalid:
        raise EmbeddingProviderError(
            "EMBEDDING_INVALID_OUTPUT",
            "Embedding model returned an invalid vector",
        )
    return vectors


def load_sentence_transformer(config: EmbeddingConfig) -> SentenceEmbeddingModel:
    if config.endpoint:
        os.environ["HF_ENDPOINT"] = config.endpoint
    os.environ["HF_HOME"] = str(config.cache_dir)

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        config.model,
        revision=config.revision,
        cache_folder=str(config.cache_dir),
        device=config.device,
    )


class BgeM3EmbeddingProvider:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        model_loader: ModelLoader = load_sentence_transformer,
    ) -> None:
        self.config = config
        self._model_loader = model_loader
        self._model: SentenceEmbeddingModel | None = None
        self._model_lock = Lock()

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        try:
            model = await asyncio.to_thread(self._get_model)
            raw_vectors = await asyncio.to_thread(
                model.encode,
                list(texts),
                batch_size=self.config.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        except EmbeddingProviderError:
            raise
        except Exception as error:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER_UNAVAILABLE",
                "Embedding model is temporarily unavailable",
            ) from error

        return validate_embeddings(
            raw_vectors,
            expected_count=len(texts),
            dimension=self.config.dimension,
        )

    def _get_model(self) -> SentenceEmbeddingModel:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                self._model = self._model_loader(self.config)
                dimension = self._model.get_embedding_dimension()
                if dimension != self.config.dimension:
                    raise EmbeddingProviderError(
                        "EMBEDDING_INVALID_OUTPUT",
                        "Embedding model returned an invalid vector",
                    )
        return self._model
