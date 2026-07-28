from pathlib import Path

from sourcetrace.core.config import Settings
from sourcetrace.modules.documents.models import IngestionRun
from sourcetrace.workers.tasks import embedding_config_for_run


def test_worker_reuses_recorded_embedding_semantics_after_configuration_changes() -> None:
    run = IngestionRun(
        document_version_id="006871ac-1c11-4854-ae6e-084a67cac73a",
        run_number=1,
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
        embedding_provider="recorded-provider",
        embedding_model="recorded-model",
        embedding_model_revision="recorded-revision",
        embedding_dimension=1024,
        embedding_config_version="recorded-config-v1",
    )
    settings = Settings(
        embedding_provider="changed-provider",
        embedding_model="changed-model",
        embedding_model_revision="changed-revision",
        embedding_cache_dir=Path("changed-cache"),
        embedding_hf_endpoint="https://changed.invalid",
        embedding_device="cuda",
        embedding_batch_size=16,
        embedding_dimension=2048,
        embedding_config_version="changed-config-v2",
    )

    config = embedding_config_for_run(settings, run)

    assert config.provider == "recorded-provider"
    assert config.model == "recorded-model"
    assert config.revision == "recorded-revision"
    assert config.dimension == 1024
    assert config.version == "recorded-config-v1"
    assert config.cache_dir == Path("changed-cache")
    assert config.device == "cuda"
    assert config.batch_size == 16
