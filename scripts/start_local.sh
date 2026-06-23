#!/usr/bin/env bash
# Start the full local stack in detached mode, run first-run setup, then tail logs.
# Usage: ./scripts/start_local.sh [--no-logs]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE="docker compose -f $REPO_ROOT/docker/docker-compose.yml"

cd "$REPO_ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example and fill in your API keys."
  exit 1
fi

echo "==> Building images and starting all services (detached)..."
$COMPOSE up --build -d

echo "==> Waiting for api service to be ready..."
until $COMPOSE exec -T api python -c "import triage" 2>/dev/null; do
  sleep 2
done

echo "==> Running database migrations..."
$COMPOSE exec -T api alembic upgrade head

# Only ingest if the vector store appears empty (idempotent check)
DOC_COUNT=$($COMPOSE exec -T api python -c "
import sys; sys.path.insert(0, 'src')
from triage.db.session import get_session
from sqlalchemy import text
with next(get_session()) as s:
    n = s.execute(text('SELECT COUNT(*) FROM document_chunks')).scalar()
    print(n)
" 2>/dev/null || echo "0")

if [ "$DOC_COUNT" = "0" ]; then
  echo "==> Ingesting knowledge base into pgvector (first run)..."
  $COMPOSE exec -T api python scripts/ingest_docs.py
else
  echo "==> Knowledge base already ingested ($DOC_COUNT chunks). Skipping."
fi

echo ""
echo "Stack is up:"
echo "  Demo UI   -> http://localhost:8501"
echo "  API       -> http://localhost:8000"
echo "  Metrics   -> http://localhost:9091/metrics"
echo ""

if [[ "${1:-}" != "--no-logs" ]]; then
  echo "Tailing logs (Ctrl-C to stop, services keep running)..."
  $COMPOSE logs -f api worker demo
fi
