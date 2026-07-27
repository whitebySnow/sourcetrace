from uuid import uuid4

from sourcetrace.core.config import Settings
from sourcetrace.modules.documents.models import IngestionRun
from sourcetrace.modules.documents.queue import DramatiqIngestionQueue
from sourcetrace.workers.tasks import chunking_config_for_run, ingest_document_version


class RecordingActor:
    def __init__(self) -> None:
        self.arguments: list[str] = []

    def send(self, version_id: str) -> object:
        self.arguments.append(version_id)
        return object()


class EventuallyAvailableActor(RecordingActor):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def send(self, version_id: str) -> object:
        self.attempts += 1
        if self.attempts < 3:
            raise ConnectionError
        return super().send(version_id)


async def test_queue_message_contains_only_the_document_version_identifier() -> None:
    actor = RecordingActor()
    queue = DramatiqIngestionQueue(actor)
    version_id = uuid4()

    await queue.enqueue(version_id)

    assert actor.arguments == [str(version_id)]


def test_worker_has_two_retries_for_three_total_attempts_with_backoff() -> None:
    assert ingest_document_version.options["max_retries"] == 2
    assert ingest_document_version.options["min_backoff"] > 0
    assert ingest_document_version.options["max_backoff"] >= (
        ingest_document_version.options["min_backoff"]
    )


async def test_queue_delivery_retries_three_times_with_backoff() -> None:
    actor = EventuallyAvailableActor()
    version_id = uuid4()

    await DramatiqIngestionQueue(actor).enqueue(version_id)

    assert actor.attempts == 3
    assert actor.arguments == [str(version_id)]


def test_manual_retry_executes_with_its_recorded_chunking_configuration() -> None:
    run = IngestionRun(
        document_version_id=uuid4(),
        run_number=2,
        parser_version="pypdf-v1",
        tokenizer="cl100k_base",
        chunk_size=400,
        chunk_overlap=60,
        chunking_config_version="token-window-v1",
    )
    settings = Settings(
        ingestion_tokenizer="o200k_base",
        ingestion_chunk_size=900,
        ingestion_chunk_overlap=90,
        ingestion_chunking_config_version="token-window-v2",
    )

    config = chunking_config_for_run(settings, run)

    assert config.tokenizer == "cl100k_base"
    assert config.chunk_size == 400
    assert config.chunk_overlap == 60
    assert config.version == "token-window-v1"
