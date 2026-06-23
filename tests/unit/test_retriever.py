"""Unit tests for the retriever.

Mocks out the Voyage embedder and SQLAlchemy Session so no real
DB or API calls are made. Tests cover result formatting, category
filtering (ilike not exact match), and rate-limit retry logic.
"""

from unittest.mock import MagicMock, patch

import pytest

from triage.db.models import DocumentChunk
from triage.retrieval.retriever import retrieve

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(source_file: str = "refund_policy.md", content: str = "policy text") -> MagicMock:
    # spec=DocumentChunk makes MagicMock pass Pydantic's isinstance check.
    chunk = MagicMock(spec=DocumentChunk)
    chunk.source_file = source_file
    chunk.content = content
    return chunk


def _make_session_cm(rows: list[tuple]) -> MagicMock:
    """Return a mock Session context manager whose execute().all() returns rows."""
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    # Make Session(engine) usable as a context manager.
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, session


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


class TestResultFormatting:
    def test_returns_chunk_with_score_objects(self) -> None:
        chunk = _make_chunk()
        cm, session = _make_session_cm([(chunk, 0.82)])

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            results = retrieve("I want a refund")

        assert len(results) == 1
        assert results[0].chunk is chunk
        assert results[0].score == pytest.approx(0.82)

    def test_score_is_float(self) -> None:
        chunk = _make_chunk()
        cm, session = _make_session_cm([(chunk, 0.75)])

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            results = retrieve("query")

        assert isinstance(results[0].score, float)

    def test_expunge_called_for_each_result(self) -> None:
        chunks = [_make_chunk("a.md"), _make_chunk("b.md")]
        cm, session = _make_session_cm([(chunks[0], 0.9), (chunks[1], 0.7)])

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            retrieve("query")

        assert session.expunge.call_count == 2
        session.expunge.assert_any_call(chunks[0])
        session.expunge.assert_any_call(chunks[1])

    def test_empty_results_returned_without_error(self) -> None:
        cm, _ = _make_session_cm([])

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            results = retrieve("query with no matches")

        assert results == []


# ---------------------------------------------------------------------------
# Embedder call
# ---------------------------------------------------------------------------


class TestEmbedderCall:
    def test_embed_query_called_with_input_text(self) -> None:
        cm, _ = _make_session_cm([])

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.0] * 512
            retrieve("customer wants refund for broken laptop")

        mock_emb.embed_query.assert_called_once_with("customer wants refund for broken laptop")


# ---------------------------------------------------------------------------
# Category filter - ilike not exact match
# ---------------------------------------------------------------------------


class TestCategoryFilter:
    def test_known_category_adds_where_clause(self) -> None:
        """When category='refund' is provided, the SQL statement gains a WHERE clause."""
        cm_with, session_with = _make_session_cm([])
        cm_without, session_without = _make_session_cm([])

        # Capture the stmt argument passed to execute()
        stmts_with: list = []
        stmts_without: list = []

        def capture_with(stmt):
            stmts_with.append(stmt)
            return MagicMock(all=lambda: [])

        def capture_without(stmt):
            stmts_without.append(stmt)
            return MagicMock(all=lambda: [])

        session_with.execute = capture_with
        session_without.execute = capture_without

        with patch("triage.retrieval.retriever._embedder") as mock_emb:
            mock_emb.embed_query.return_value = [0.1] * 512

            with patch("triage.retrieval.retriever.Session", return_value=cm_with):
                retrieve("refund query", category="refund")

            with patch("triage.retrieval.retriever.Session", return_value=cm_without):
                retrieve("refund query", category=None)

        # Both captured exactly one statement.
        assert len(stmts_with) == 1
        assert len(stmts_without) == 1

        # The filtered statement should have a WHERE clause; the unfiltered should not.
        sql_with = str(stmts_with[0]).lower()
        sql_without = str(stmts_without[0]).lower()
        assert "where" in sql_with
        assert "where" not in sql_without

    def test_category_filter_uses_ilike_not_exact(self) -> None:
        cm, session = _make_session_cm([])
        captured: list = []
        session.execute = lambda stmt: (captured.append(stmt), MagicMock(all=lambda: []))[1]

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            retrieve("query", category="refund")

        sql = str(captured[0]).lower()
        assert "like" in sql  # ilike compiles to LIKE or ILIKE depending on dialect

    def test_unknown_category_falls_back_to_no_filter(self) -> None:
        """Unknown category name should not crash - falls back to global search."""
        cm_unknown, session_unknown = _make_session_cm([])
        cm_none, session_none = _make_session_cm([])

        stmts_unknown: list = []
        stmts_none: list = []

        session_unknown.execute = lambda s: (stmts_unknown.append(s), MagicMock(all=lambda: []))[1]
        session_none.execute = lambda s: (stmts_none.append(s), MagicMock(all=lambda: []))[1]

        with patch("triage.retrieval.retriever._embedder") as mock_emb:
            mock_emb.embed_query.return_value = [0.1] * 512

            with patch("triage.retrieval.retriever.Session", return_value=cm_unknown):
                retrieve("query", category="completely_unknown")

            with patch("triage.retrieval.retriever.Session", return_value=cm_none):
                retrieve("query", category=None)

        # Both should produce the same WHERE-clause-free statement.
        sql_unknown = str(stmts_unknown[0]).lower()
        sql_none = str(stmts_none[0]).lower()
        assert "where" not in sql_unknown
        assert "where" not in sql_none

    @pytest.mark.parametrize("category", ["refund", "technical", "billing", "account"])
    def test_all_known_categories_add_filter(self, category: str) -> None:
        cm, session = _make_session_cm([])
        captured: list = []
        session.execute = lambda stmt: (captured.append(stmt), MagicMock(all=lambda: []))[1]

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
        ):
            mock_emb.embed_query.return_value = [0.1] * 512
            retrieve("query", category=category)

        sql = str(captured[0]).lower()
        # The category value goes into a bind parameter, not the SQL string directly.
        # Verify a WHERE clause was added with case-insensitive matching.
        assert "where" in sql
        assert "like" in sql


# ---------------------------------------------------------------------------
# Rate limit retry
# ---------------------------------------------------------------------------


class TestRateLimitRetry:
    def test_rate_limit_error_triggers_retry(self) -> None:
        cm, _ = _make_session_cm([])

        class RateLimitError(Exception):
            pass

        call_count = 0

        def flaky_embed(query: str) -> list[float]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError("rate limit exceeded")
            return [0.1] * 512

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.Session", return_value=cm),
            patch("triage.retrieval.retriever.time") as mock_time,
        ):
            mock_emb.embed_query.side_effect = flaky_embed
            retrieve("query")

        assert call_count == 2
        mock_time.sleep.assert_called_once()

    def test_non_rate_limit_error_is_not_retried(self) -> None:
        class OtherError(Exception):
            pass

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.time"),
        ):
            mock_emb.embed_query.side_effect = OtherError("something else broke")
            with pytest.raises(OtherError):
                retrieve("query")

        assert mock_emb.embed_query.call_count == 1

    def test_exhausted_retries_raise_runtime_error(self) -> None:
        class RateLimitError(Exception):
            pass

        with (
            patch("triage.retrieval.retriever._embedder") as mock_emb,
            patch("triage.retrieval.retriever.time"),
        ):
            mock_emb.embed_query.side_effect = RateLimitError("rate limit")
            with pytest.raises(RuntimeError, match="all retries"):
                retrieve("query")
