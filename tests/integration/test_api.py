"""Integration tests for the FastAPI ticket endpoints.

Uses httpx.AsyncClient with ASGITransport so requests go through the full
FastAPI stack without binding a port. DB and Redis are replaced via
FastAPI dependency_overrides so no infrastructure is required.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from triage.api.main import app
from triage.api.routes.tickets import get_db, get_redis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
_TICKET_UUID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_begin_cm() -> MagicMock:
    """Return an async context manager that simulates session.begin()."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_fake_ticket(
    *,
    status: str = "pending",
    response: str | None = None,
    escalation_records: list | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = _TICKET_UUID
    t.status = status
    t.response = response
    t.created_at = _NOW
    t.resolved_at = None
    t.escalation_records = escalation_records or []
    return t


def _make_fake_session(ticket: MagicMock | None = None) -> MagicMock:
    """Return a fake AsyncSession.

    ticket is the value returned by scalar_one_or_none(); pass None to
    simulate a not-found query result.
    """
    s = MagicMock()
    # session.begin() must return an async context manager, not a coroutine.
    s.begin = MagicMock(return_value=_make_begin_cm())
    s.add = MagicMock()
    s.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = ticket
    s.execute = AsyncMock(return_value=result)
    return s


def _override_db(session: MagicMock) -> None:
    async def _dep():
        yield session

    app.dependency_overrides[get_db] = _dep


def _override_redis(redis_mock: AsyncMock) -> None:
    async def _dep():
        return redis_mock

    app.dependency_overrides[get_redis] = _dep


def _make_redis(*, fail: bool = False) -> AsyncMock:
    r = AsyncMock()
    if fail:
        r.xadd = AsyncMock(side_effect=Exception("Redis down"))
    else:
        r.xadd = AsyncMock(return_value=b"1-0")
    return r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def clear_dep_overrides():
    """Ensure dependency overrides do not leak between tests."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /tickets
# ---------------------------------------------------------------------------


class TestCreateTicket:
    async def test_returns_202_with_ticket_id(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket()
        _override_db(_make_fake_session())
        _override_redis(_make_redis())

        with patch("triage.api.routes.tickets.Ticket", return_value=fake_ticket):
            resp = await client.post("/tickets", json={"content": "I need a refund."})

        assert resp.status_code == 202
        body = resp.json()
        assert body["ticket_id"] == str(_TICKET_UUID)
        assert body["status"] == "pending"

    async def test_xadd_called_with_ticket_id_and_content(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket()
        redis = _make_redis()
        _override_db(_make_fake_session())
        _override_redis(redis)

        with patch("triage.api.routes.tickets.Ticket", return_value=fake_ticket):
            await client.post("/tickets", json={"content": "Help with billing."})

        redis.xadd.assert_called_once()
        fields = redis.xadd.call_args[0][1]
        assert "ticket_id" in fields
        assert fields["content"] == "Help with billing."

    async def test_rejects_empty_content(self, client: AsyncClient) -> None:
        # Pydantic validates before any dependency runs.
        resp = await client.post("/tickets", json={"content": ""})
        assert resp.status_code == 422

    async def test_rejects_content_over_5000_chars(self, client: AsyncClient) -> None:
        resp = await client.post("/tickets", json={"content": "x" * 5001})
        assert resp.status_code == 422

    async def test_accepts_content_of_exactly_5000_chars(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket()
        _override_db(_make_fake_session())
        _override_redis(_make_redis())

        with patch("triage.api.routes.tickets.Ticket", return_value=fake_ticket):
            resp = await client.post("/tickets", json={"content": "x" * 5000})

        assert resp.status_code == 202

    async def test_returns_503_when_redis_fails(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket()
        _override_db(_make_fake_session())
        _override_redis(_make_redis(fail=True))

        with patch("triage.api.routes.tickets.Ticket", return_value=fake_ticket):
            resp = await client.post("/tickets", json={"content": "I need a refund."})

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /tickets/{ticket_id}
# ---------------------------------------------------------------------------


class TestGetTicket:
    async def test_returns_ticket_fields(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket(status="pending")
        _override_db(_make_fake_session(fake_ticket))

        resp = await client.get(f"/tickets/{_TICKET_UUID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ticket_id"] == str(_TICKET_UUID)
        assert body["status"] == "pending"
        assert body["escalated"] is False
        assert body["escalation_reason"] is None

    async def test_404_for_unknown_ticket(self, client: AsyncClient) -> None:
        _override_db(_make_fake_session(None))
        resp = await client.get(f"/tickets/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_404_for_malformed_uuid(self, client: AsyncClient) -> None:
        # UUID parse raises ValueError before any DB call is made.
        _override_db(_make_fake_session())
        resp = await client.get("/tickets/not-a-uuid")
        assert resp.status_code == 404

    async def test_escalated_true_when_escalation_record_exists(self, client: AsyncClient) -> None:
        record = MagicMock()
        record.reason = "Router confidence too low"
        record.created_at = _NOW
        fake_ticket = _make_fake_ticket(status="escalated", escalation_records=[record])
        _override_db(_make_fake_session(fake_ticket))

        resp = await client.get(f"/tickets/{_TICKET_UUID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["escalated"] is True
        assert body["escalation_reason"] == "Router confidence too low"

    async def test_escalated_false_when_no_escalation_record(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket(status="resolved", escalation_records=[])
        _override_db(_make_fake_session(fake_ticket))

        resp = await client.get(f"/tickets/{_TICKET_UUID}")

        assert resp.json()["escalated"] is False


# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------


class TestApiKeyAuth:
    async def test_development_mode_skips_auth(self, client: AsyncClient) -> None:
        # Default environment is "development" - invalid body returns 422, not 401.
        resp = await client.post("/tickets", json={"content": ""})
        assert resp.status_code == 422

    async def test_production_mode_rejects_missing_key(self, client: AsyncClient) -> None:
        with patch("triage.api.routes.tickets.settings") as mock_settings:
            mock_settings.environment = "production"
            mock_settings.api_key = "secret"
            resp = await client.post("/tickets", json={"content": "test"})
        assert resp.status_code == 401

    async def test_production_mode_rejects_wrong_key(self, client: AsyncClient) -> None:
        with patch("triage.api.routes.tickets.settings") as mock_settings:
            mock_settings.environment = "production"
            mock_settings.api_key = "secret"
            resp = await client.post(
                "/tickets",
                json={"content": "test"},
                headers={"X-API-Key": "wrong"},
            )
        assert resp.status_code == 401

    async def test_production_mode_accepts_correct_key(self, client: AsyncClient) -> None:
        fake_ticket = _make_fake_ticket()
        _override_db(_make_fake_session())
        _override_redis(_make_redis())

        with (
            patch("triage.api.routes.tickets.settings") as mock_settings,
            patch("triage.api.routes.tickets.Ticket", return_value=fake_ticket),
        ):
            mock_settings.environment = "production"
            mock_settings.api_key = "secret"
            mock_settings.redis_stream_name = "tickets"
            resp = await client.post(
                "/tickets",
                json={"content": "I need a refund."},
                headers={"X-API-Key": "secret"},
            )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
