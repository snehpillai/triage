#!/usr/bin/env python3
"""Worker entry point.

Run from repo root:
    python scripts/run_worker.py

Set WORKER_NAME env var to override the consumer name (default: hostname).
Set DATABASE_URL and REDIS_URL to point at non-default infrastructure.
"""

import sys

sys.path.insert(0, "src")

from loguru import logger
from prometheus_client import start_http_server

from triage.observability import setup_tracing
from triage.queue.consumer import TicketConsumer

_METRICS_PORT = 9091

if __name__ == "__main__":
    setup_tracing()
    start_http_server(_METRICS_PORT)
    logger.info("Worker metrics exposed at http://localhost:{p}/", p=_METRICS_PORT)
    consumer = TicketConsumer()
    try:
        consumer.run()
    except Exception as exc:
        logger.exception("Worker crashed: {e}", e=exc)
        sys.exit(1)
