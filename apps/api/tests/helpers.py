from collections.abc import Sequence

from sourcetrace.rag.ports import RerankerIdentity


class PreserveOrderReranker:
    identity = RerankerIdentity(
        provider="test",
        model="preserve-order",
        revision="v1",
        config_version="preserve-order-v1",
    )

    async def score(
        self,
        *,
        question: str,
        passages: Sequence[str],
    ) -> Sequence[float]:
        return tuple(float(len(passages) - index) for index in range(len(passages)))
