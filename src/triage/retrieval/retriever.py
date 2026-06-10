from loguru import logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from triage.config import settings
from triage.db.models import DocumentChunk
from triage.retrieval.embedder import VoyageEmbedder

# One engine per process - holds the connection pool.
_engine = create_engine(settings.database_url)
_embedder = VoyageEmbedder()

# Maps the intent category name used by agents to the source file stored in the DB.
# source_file is written by scripts/ingest_docs.py as the bare filename (no path).
_CATEGORY_TO_FILE: dict[str, str] = {
    "refund": "refund_policy.md",
    "technical": "technical_faq.md",
    "billing": "billing_faq.md",
    "account": "account_faq.md",
}


class ChunkWithScore(BaseModel):
    # arbitrary_types_allowed lets Pydantic hold a SQLAlchemy model instance
    # without trying to serialise it. ChunkWithScore is an internal result
    # type - it is never converted to JSON directly.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    chunk: DocumentChunk
    score: float  # cosine similarity: 1.0 = identical, 0.0 = orthogonal


def retrieve(
    query: str,
    category: str | None = None,
    top_k: int = 5,
) -> list[ChunkWithScore]:
    """Search the knowledge base for chunks relevant to query.

    Args:
        query: The customer message or a focused sub-question derived from it.
        category: Restricts search to one source file. Pass the intent string
            ("refund", "technical", "billing", "account"). Unknown values fall
            back to a global search with a logged warning.
        top_k: Maximum number of chunks to return, ordered by descending score.

    Returns:
        ChunkWithScore list ordered best-first. A score below ~0.5 means the
        query has weak overlap with the knowledge base - the QC agent uses this
        to flag responses where the specialist may be extrapolating.
    """
    query_vec: list[float] = _embedder.embed_query(query)

    # cosine_distance() emits the pgvector <=> operator.
    # <=> returns cosine *distance* (0 = identical, 2 = opposite).
    # We ORDER BY distance ASC (closest first) and compute 1 - distance
    # as the score so callers get an intuitive similarity value.
    dist = DocumentChunk.embedding.cosine_distance(query_vec)
    stmt = select(DocumentChunk, (1 - dist).label("score")).order_by(dist).limit(top_k)

    if category is not None:
        source_file = _CATEGORY_TO_FILE.get(category)
        if source_file is None:
            logger.warning(
                "Unknown category '{cat}' passed to retrieve() - searching all files",
                cat=category,
            )
        else:
            stmt = stmt.where(DocumentChunk.source_file == source_file)

    with Session(_engine) as session:
        rows = session.execute(stmt).all()
        # expunge() detaches each instance from the session while keeping its
        # already-loaded attributes readable. Without this, accessing chunk
        # attributes after the session closes raises DetachedInstanceError.
        results: list[ChunkWithScore] = []
        for chunk, score in rows:
            session.expunge(chunk)
            results.append(ChunkWithScore(chunk=chunk, score=float(score)))

    logger.debug(
        "retrieve: {n} chunks, category={cat}, top_score={top:.3f}, query='{q}'",
        n=len(results),
        cat=category,
        top=results[0].score if results else 0.0,
        q=query[:60],
    )

    return results
