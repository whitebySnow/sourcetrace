import asyncio
import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

from sourcetrace.rag.ports import RerankerIdentity


class RerankerProviderError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class RerankerConfig:
    provider: str
    model: str
    revision: str
    weight_sha256: str
    cache_dir: Path
    device: str
    batch_size: int
    version: str

    def __post_init__(self) -> None:
        if self.batch_size <= 0:
            raise ValueError("reranker batch size must be positive")
        if len(self.weight_sha256) != 64:
            raise ValueError("reranker weight SHA-256 must contain 64 characters")

    @property
    def model_path(self) -> Path:
        return self.cache_dir.joinpath(*self.model.split("/"))

    @property
    def identity(self) -> RerankerIdentity:
        return RerankerIdentity(
            provider=self.provider,
            model=self.model,
            revision=self.revision,
            config_version=self.version,
        )


class CrossEncoderModel(Protocol):
    def predict(self, inputs: list[tuple[str, str]], **kwargs: Any) -> object: ...


ModelLoader = Callable[[RerankerConfig], CrossEncoderModel]


def load_cross_encoder(config: RerankerConfig) -> CrossEncoderModel:
    model_path = config.model_path
    weight_path = model_path / "model.safetensors"
    if not model_path.is_dir() or not weight_path.is_file():
        raise RerankerProviderError(
            "RERANKER_MODEL_NOT_FOUND",
            "Reranker model is not available on this deployment",
        )
    with weight_path.open("rb") as source:
        actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    if actual_sha256 != config.weight_sha256.lower():
        raise RerankerProviderError(
            "RERANKER_MODEL_INVALID",
            "Reranker model integrity check failed",
        )

    from sentence_transformers import CrossEncoder

    return CrossEncoder(str(model_path), device=config.device, local_files_only=True)


class BgeCrossEncoderReranker:
    def __init__(
        self,
        config: RerankerConfig,
        *,
        model_loader: ModelLoader = load_cross_encoder,
    ) -> None:
        self.config = config
        self._model_loader = model_loader
        self._model: CrossEncoderModel | None = None
        self._model_lock = Lock()
        self._inference_lock = asyncio.Lock()

    @property
    def identity(self) -> RerankerIdentity:
        return self.config.identity

    async def score(
        self,
        *,
        question: str,
        passages: Sequence[str],
    ) -> Sequence[float]:
        if not question.strip():
            raise ValueError("reranker question must not be blank")
        if not passages:
            return ()
        try:
            model = await asyncio.to_thread(self._get_model)
            async with self._inference_lock:
                raw_scores = await asyncio.to_thread(
                    model.predict,
                    [(question, passage) for passage in passages],
                    batch_size=self.config.batch_size,
                    show_progress_bar=False,
                )
            values = raw_scores.tolist() if hasattr(raw_scores, "tolist") else raw_scores
            scores = tuple(float(score) for score in cast(Sequence[Any], values))
        except RerankerProviderError:
            raise
        except Exception as error:
            raise RerankerProviderError(
                "RERANKER_PROVIDER_UNAVAILABLE",
                "Reranker model is temporarily unavailable",
            ) from error
        if len(scores) != len(passages) or not all(math.isfinite(score) for score in scores):
            raise RerankerProviderError(
                "RERANKER_INVALID_OUTPUT",
                "Reranker model returned an invalid score",
            )
        return scores

    def _get_model(self) -> CrossEncoderModel:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                self._model = self._model_loader(self.config)
        return self._model
