import pytest

from sourcetrace.core.config import Settings
from sourcetrace.evaluation.real import _resolve_embedding_model
from sourcetrace.evaluation.repository import CorpusProvenance


def corpus_provenance() -> CorpusProvenance:
    return CorpusProvenance(
        parser_version="pypdf-v2",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="token-window-v1",
        embedding_provider="sentence-transformers",
        embedding_model="/models/huggingface/modelscope/BAAI/bge-m3",
        embedding_revision="test-revision",
        embedding_dimension=1024,
        embedding_version="bge-m3-dense-v1",
    )


def test_embedding_replay_uses_current_runtime_path_for_the_same_model() -> None:
    settings = Settings(
        embedding_model=r"D:\DevelopEnvironment\huggingface\modelscope\BAAI\bge-m3",
        embedding_model_revision="test-revision",
        embedding_dimension=1024,
        embedding_config_version="bge-m3-dense-v1",
    )

    model = _resolve_embedding_model(corpus_provenance(), settings)

    assert model == settings.embedding_model


def test_embedding_replay_rejects_a_different_runtime_revision() -> None:
    settings = Settings(
        embedding_model=r"D:\DevelopEnvironment\huggingface\modelscope\BAAI\bge-m3",
        embedding_model_revision="different-revision",
        embedding_dimension=1024,
        embedding_config_version="bge-m3-dense-v1",
    )

    with pytest.raises(RuntimeError, match="embedding revision"):
        _resolve_embedding_model(corpus_provenance(), settings)
