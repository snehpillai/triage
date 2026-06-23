"""Session-wide test configuration.

Sets minimal environment variables before any triage module is imported so the
Settings object can be constructed without a .env file in CI.
Real values are only needed for integration tests (marked with @pytest.mark.integration).
"""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used-in-unit-tests")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used-in-unit-tests")
os.environ.setdefault("VOYAGE_API_KEY", "test-key-not-used-in-unit-tests")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@localhost:5433/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
