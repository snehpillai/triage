from pydantic import BaseModel, ConfigDict

from triage.db.models import DocumentChunk


class ChunkWithScore(BaseModel):
    # arbitrary_types_allowed lets Pydantic hold a SQLAlchemy model instance
    # without trying to serialise it. ChunkWithScore is an internal result
    # type - it is never converted to JSON directly.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    chunk: DocumentChunk
    score: float  # cosine similarity: 1.0 = identical, 0.0 = orthogonal
