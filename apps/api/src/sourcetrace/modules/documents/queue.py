import asyncio
from typing import Protocol
from uuid import UUID

from sourcetrace.modules.documents.service import IngestionQueueUnavailableError
from sourcetrace.workers.tasks import ingest_document_version

QUEUE_SEND_ATTEMPTS = 3
QUEUE_SEND_BACKOFF_SECONDS = 0.1


class ActorPort(Protocol):
    def send(self, version_id: str) -> object: ...


class DramatiqIngestionQueue:
    def __init__(self, actor: ActorPort = ingest_document_version) -> None:
        self._actor = actor

    async def enqueue(self, version_id: UUID) -> None:
        for attempt in range(QUEUE_SEND_ATTEMPTS):
            try:
                await asyncio.to_thread(self._actor.send, str(version_id))
                return
            except Exception as error:
                if attempt == QUEUE_SEND_ATTEMPTS - 1:
                    raise IngestionQueueUnavailableError from error
                await asyncio.sleep(QUEUE_SEND_BACKOFF_SECONDS * (2**attempt))
