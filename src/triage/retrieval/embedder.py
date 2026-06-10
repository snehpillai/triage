import time

import voyageai
from loguru import logger

from triage.config import settings

_MODEL = "voyage-3-large"

# Voyage's own published batch limit for this model. Using their constant
# keeps us in sync if the library updates it.
_BATCH_SIZE: int = voyageai.VOYAGE_EMBED_BATCH_SIZE  # 128

# The SDK's embed() method uses tenacity internally:
#   stop=stop_after_attempt(max_retries)
#   wait=wait_exponential_jitter(initial=1s, max=16s)
#   retry on RateLimitError | ServiceUnavailableError | Timeout
# Setting max_retries > 0 at construction activates this. Default is 0 (no retries).
_MAX_RETRIES = 4


class VoyageEmbedder:
    def __init__(self) -> None:
        self._client = voyageai.Client(
            api_key=settings.voyage_api_key,
            max_retries=_MAX_RETRIES,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of document texts in batches of up to 128.

        Uses input_type='document' - Voyage scores document embeddings
        differently from query embeddings to improve retrieval precision.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        total_batches = -(-len(texts) // _BATCH_SIZE)  # ceiling division

        for batch_num, start in enumerate(range(0, len(texts), _BATCH_SIZE), start=1):
            batch = texts[start : start + _BATCH_SIZE]
            logger.debug(
                "Embedding document batch {batch}/{total} ({n} texts)",
                batch=batch_num,
                total=total_batches,
                n=len(batch),
            )
            t0 = time.monotonic()
            result = self._client.embed(texts=batch, model=_MODEL, input_type="document")
            logger.debug(
                "Batch {batch}/{total} done in {elapsed:.2f}s",
                batch=batch_num,
                total=total_batches,
                elapsed=time.monotonic() - t0,
            )
            all_embeddings.extend(result.embeddings)

        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string.

        Uses input_type='query' - Voyage optimises query vectors differently
        from document vectors. Using 'document' here would degrade recall by
        roughly 5-15% on retrieval benchmarks.
        """
        result = self._client.embed(texts=[text], model=_MODEL, input_type="query")
        return result.embeddings[0]
