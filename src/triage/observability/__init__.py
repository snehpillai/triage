"""Observability utilities: LangSmith tracing setup and Prometheus metrics.

Importing this package registers all Prometheus metric collectors with the
default registry. Both the API process (which imports setup_tracing) and the
worker (same) therefore have metrics registered before the first tick.
"""

import os

import langsmith
from loguru import logger

from triage.config import settings
from triage.observability import (
    metrics as _metrics,  # noqa: F401, TCH001 - register Prometheus collectors
)


def setup_tracing() -> None:
    """Configure LangSmith tracing from settings.

    Sets the environment variables that LangSmith's Client reads on first use,
    then calls langsmith.configure() to apply project and enabled state via
    context variables. No-ops when langchain_tracing_v2=False or no API key.

    Call this once at process start (API startup, worker startup) before any
    LangGraph invocations occur.
    """
    if not settings.langchain_tracing_v2 or not settings.langchain_api_key:
        logger.debug(
            "LangSmith tracing disabled (set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY to enable)"
        )
        return

    # pydantic-settings reads .env into the Settings object but does NOT
    # propagate values to os.environ. LangSmith's Client reads from os.environ,
    # so we bridge the gap here.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key

    langsmith.configure(
        enabled=True,
        project_name=settings.langchain_project,
    )

    logger.info("LangSmith tracing enabled: project={p}", p=settings.langchain_project)
