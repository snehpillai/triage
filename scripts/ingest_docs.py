"""Chunk, embed, and upsert knowledge base documents into pgvector.

Run from the project root:
    python scripts/ingest_docs.py

Idempotent: re-running deletes and re-inserts chunks for each file.
Embed first, write to DB second — if Voyage fails, the DB is unchanged.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import time

import tiktoken
from loguru import logger
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from triage.config import settings
from triage.db.models import DocumentChunk
from triage.retrieval.embedder import VoyageEmbedder

KB_DIR = Path(__file__).parent.parent / "data" / "knowledge_base"
CHUNK_SIZE = 500  # target tokens per chunk
OVERLAP = 50  # token overlap between consecutive chunks
MIN_CHUNK_TOKENS = 20  # discard title-only or near-empty sections

# voyage-3-large pricing — verify current rate at dash.voyageai.com/pricing
_COST_PER_MILLION_TOKENS = 0.18

# Free-tier Voyage accounts are limited to 3 RPM. Adding a payment method on
# dash.voyageai.com/billing unlocks higher limits (200M free tokens still apply).
# Set to 0 once a payment method is added.
_INTER_FILE_SLEEP_SECONDS = 21

# cl100k_base is GPT-4's tokenizer. Voyage's tokenizer differs slightly,
# but the counts are close enough for chunk sizing. Using tiktoken avoids
# adding Voyage's tokenizer as a hard dependency.
_enc = tiktoken.get_encoding("cl100k_base")


def _sliding_window(text: str) -> list[str]:
    tokens = _enc.encode(text)
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE, len(tokens))
        chunks.append(_enc.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += CHUNK_SIZE - OVERLAP
    return chunks


def chunk_text(text: str) -> list[str]:
    """Split on H2/H3 markdown headers first, then sliding window within long sections.

    Keeps each policy section as its own chunk so retrieval returns focused,
    single-topic text rather than a mix of unrelated sections. The sliding
    window only activates for sections that exceed CHUNK_SIZE tokens.
    """
    sections = re.split(r"(?=^#{2,3} )", text, flags=re.MULTILINE)
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        token_count = len(_enc.encode(section))
        if token_count < MIN_CHUNK_TOKENS:
            continue  # discard document/section title stubs
        if token_count <= CHUNK_SIZE:
            chunks.append(section)
        else:
            chunks.extend(_sliding_window(section))
    return chunks


def ingest_file(
    filepath: Path,
    engine: object,
    embedder: VoyageEmbedder,
) -> tuple[int, int]:
    """Process one file. Returns (chunk_count, token_count)."""
    source_file = filepath.name
    text = filepath.read_text(encoding="utf-8")
    chunks = chunk_text(text)

    if not chunks:
        logger.warning("{file}: produced 0 chunks, skipping", file=source_file)
        return 0, 0

    token_count = sum(len(_enc.encode(c)) for c in chunks)

    logger.info(
        "{file}: {n} chunks, {t} tokens — embedding...",
        file=source_file,
        n=len(chunks),
        t=token_count,
    )

    # Embed before touching the DB. If this raises, the DB is unchanged.
    embeddings = embedder.embed_documents(chunks)

    # Atomic upsert: delete old rows, insert new ones, commit together.
    with Session(engine) as session:
        session.execute(delete(DocumentChunk).where(DocumentChunk.source_file == source_file))
        for idx, (content, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            session.add(
                DocumentChunk(
                    source_file=source_file,
                    chunk_index=idx,
                    content=content,
                    embedding=embedding,
                )
            )
        session.commit()

    logger.info("{file}: done", file=source_file)
    return len(chunks), token_count


def main() -> None:
    md_files = sorted(KB_DIR.glob("*.md"))
    if not md_files:
        logger.error("No .md files found in {dir}", dir=KB_DIR)
        sys.exit(1)

    engine = create_engine(settings.database_url)
    embedder = VoyageEmbedder()

    logger.info("Found {n} documents in {dir}", n=len(md_files), dir=KB_DIR)

    total_chunks = 0
    total_tokens = 0

    for i, filepath in enumerate(md_files):
        chunks, tokens = ingest_file(filepath, engine, embedder)
        total_chunks += chunks
        total_tokens += tokens
        if _INTER_FILE_SLEEP_SECONDS > 0 and i < len(md_files) - 1:
            logger.info(
                "Waiting {s}s between files (free-tier rate limit)...", s=_INTER_FILE_SLEEP_SECONDS
            )
            time.sleep(_INTER_FILE_SLEEP_SECONDS)

    cost = (total_tokens / 1_000_000) * _COST_PER_MILLION_TOKENS

    logger.info("─" * 50)
    logger.info("Documents processed : {n}", n=len(md_files))
    logger.info("Chunks created      : {n}", n=total_chunks)
    logger.info("Tokens embedded     : {n:,}", n=total_tokens)
    logger.info(
        "Estimated cost      : ${cost:.5f}  (voyage-3-large @ ${rate}/M tokens)",
        cost=cost,
        rate=_COST_PER_MILLION_TOKENS,
    )


if __name__ == "__main__":
    main()
