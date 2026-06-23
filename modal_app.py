"""Modal deployment for the Triage API.

Deploy:
    modal deploy modal_app.py

Check logs:
    modal app logs triage

Architecture note
-----------------
The queue/worker pattern (Redis Streams + TicketConsumer) is the correct
production architecture and is fully functional in the local Docker stack.

For the deployed demo, SYNC_MODE=true bypasses the queue: POST /tickets runs
the LangGraph pipeline inline on the API process and returns once the ticket
is resolved. This trades throughput for simplicity - the response takes
8-15 seconds but no worker deployment is needed. Recruiters get a live URL
that works end-to-end without a separately running worker process.
"""

import modal

app = modal.App("triage")

# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("libpq-dev")
    .pip_install_from_pyproject("pyproject.toml")
    .env({"PYTHONPATH": "/root/src"})
    .workdir("/root")
    # add_local_* must come last - Modal mounts these at container start, not build time.
    .add_local_dir("src", remote_path="/root/src")
    .add_local_dir("scripts", remote_path="/root/scripts")
    .add_local_file("alembic.ini", remote_path="/root/alembic.ini")
)

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

# Create this secret in the Modal dashboard:
#   modal secret create triage-secrets \
#     ANTHROPIC_API_KEY=... \
#     OPENAI_API_KEY=... \
#     VOYAGE_API_KEY=... \
#     LANGCHAIN_API_KEY=... \
#     LANGCHAIN_TRACING_V2=true \
#     API_KEY=... \
#     DATABASE_URL=postgresql+psycopg://... \
#     REDIS_URL=rediss://... \
#     SYNC_MODE=true \
#     ENVIRONMENT=production
secrets = [modal.Secret.from_name("triage-secrets")]

# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


@app.function(
    image=image,
    secrets=secrets,
    min_containers=0,
    max_containers=3,
    timeout=120,
)
@modal.asgi_app()
def fastapi_app():
    from triage.api.main import app as _app

    return _app
