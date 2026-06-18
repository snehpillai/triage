"""Base class shared by all four specialist agents.

Each specialist subclass sets three class attributes and inherits run():
    category     - matches the retriever's _CATEGORY_TO_FILE keys
    system_prompt - the specialist-specific instructions
    tools        - subset of available @tool functions this specialist may call
"""

import json
from abc import ABC
from typing import Any, ClassVar

import anthropic
import openai
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from loguru import logger

from triage.config import settings
from triage.graph.state import TicketState
from triage.retrieval.retriever import retrieve
from triage.retrieval.types import ChunkWithScore
from triage.tools.circuit_breaker import (
    CircuitOpenError,
)
from triage.tools.circuit_breaker import (
    circuit_breaker as _cb,
)
from triage.tools.circuit_breaker import (
    open_message as _open_msg,
)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# OpenAI client used only when Anthropic returns 5xx/timeout/rate-limit.
# api_key falls back to a placeholder so the module loads even when OPENAI_API_KEY
# is not set; a real call without a valid key will raise AuthenticationError.
_oai_client = openai.OpenAI(api_key=settings.openai_api_key or "sk-not-configured")

_OPENAI_FALLBACK_MODEL = "gpt-4o-mini"

# Cap agentic loop iterations. Hitting the cap is a symptom of a broken tool
# or a prompt that keeps asking for more data, so we escalate rather than loop.
_MAX_ITERATIONS = 5

# Anthropic server-side errors that justify falling back to OpenAI.
_FALLBACK_STATUS_CODES: frozenset[int] = frozenset({500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------


def _to_anthropic_tool(t: BaseTool) -> dict[str, Any]:
    """Convert a LangChain @tool to the dict format Anthropic's API expects."""
    return {
        "name": t.name,
        "description": t.description or "",
        "input_schema": t.args_schema.model_json_schema(),
    }


def _to_oai_tools(anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_tools
    ]


def _to_oai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert accumulated Anthropic api_messages to OpenAI chat format.

    Handles three message shapes produced by the agentic loop:
      - user + string content (initial ticket or retry prefix)
      - assistant + list of Anthropic SDK block objects (TextBlock, ToolUseBlock)
      - user + list of tool_result dicts (tool execution results)
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Distinguish tool_result blocks from text blocks.
                first = content[0] if content else None
                is_tool_result = isinstance(first, dict) and first.get("type") == "tool_result"

                if is_tool_result:
                    # Each tool_result becomes its own "tool" role message.
                    for tr in content:
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": tr["tool_use_id"],
                                "content": tr["content"],
                            }
                        )
                else:
                    # Text blocks - join into a single user message.
                    parts = []
                    for b in content:
                        if hasattr(b, "type") and b.type == "text":
                            parts.append(b.text)
                        elif isinstance(b, dict) and b.get("type") == "text":
                            parts.append(b.get("text", ""))
                    result.append({"role": "user", "content": " ".join(parts)})

        elif role == "assistant":
            # content is a list of Anthropic SDK block objects appended via
            # api_messages.append({"role": "assistant", "content": response.content}).
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            for block in content:
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                if btype == "text":
                    text = getattr(block, "text", None) or (
                        block.get("text", "") if isinstance(block, dict) else ""
                    )
                    if text:
                        text_parts.append(text)
                elif btype == "tool_use":
                    bid = getattr(block, "id", None) or block.get("id")
                    bname = getattr(block, "name", None) or block.get("name")
                    binput = getattr(block, "input", None)
                    if binput is None and isinstance(block, dict):
                        binput = block.get("input", {})
                    tool_calls.append(
                        {
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": json.dumps(binput or {}),
                            },
                        }
                    )

            oai_msg: dict[str, Any] = {
                "role": "assistant",
                "content": " ".join(text_parts) or None,
            }
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            result.append(oai_msg)

    return result


def _serialize_result(result: Any) -> str:
    """Serialize a tool return value to a JSON string for the tool_result block."""
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, str):
        return result
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseSpecialist(ABC):
    category: ClassVar[str]
    system_prompt: ClassVar[str]
    tools: ClassVar[list[BaseTool]]
    model: ClassVar[str] = settings.specialist_model

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Enforce that every concrete subclass defines all three required attributes.
        # Checked at class-definition time so mistakes fail loudly and early.
        for attr in ("category", "system_prompt", "tools"):
            if attr not in cls.__dict__:
                raise TypeError(f"{cls.__name__} must define class attribute '{attr}'")

    # ------------------------------------------------------------------
    # Public entry point - called as a LangGraph node
    # ------------------------------------------------------------------

    def run(self, state: TicketState) -> dict[str, Any]:
        """Retrieve context, run the agentic loop, and return a state update dict."""
        ticket_id = state["ticket_id"]
        content = state["content"]
        retry_count = state.get("retry_count", 0)
        qc_feedback = state.get("qc_feedback", "")

        is_retry = retry_count > 0
        if is_retry:
            logger.info(
                "Specialist({cat}): retry attempt for ticket={id}, qc_feedback={fb!r}",
                cat=self.category,
                id=ticket_id,
                fb=qc_feedback,
            )

        # 1. Retrieve top-5 relevant policy chunks filtered to this specialist's domain.
        context_docs = retrieve(content, category=self.category, top_k=5)
        logger.debug(
            "Specialist({cat}): retrieved {n} chunks, top_score={s:.3f}",
            cat=self.category,
            n=len(context_docs),
            s=context_docs[0].score if context_docs else 0.0,
        )

        # 2. Build the full system prompt.
        full_system = self._build_system(context_docs)

        # 3. Convert tools to Anthropic and OpenAI formats (both prepared up front).
        anthropic_tools = [_to_anthropic_tool(t) for t in self.tools]
        oai_tools = _to_oai_tools(anthropic_tools)

        # 4. Agentic loop.
        if is_retry and qc_feedback:
            retry_prefix = (
                f"Previous attempt was rejected by quality review for the following "
                f"reason: {qc_feedback}. Address this in your revised response.\n\n"
            )
            user_content = retry_prefix + content
        else:
            user_content = content

        api_messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        tool_results: dict[str, Any] = {}
        draft_response = ""
        escalated = False
        provider = "anthropic"
        use_openai = False
        oai_messages: list[dict[str, Any]] = []

        for iteration in range(1, _MAX_ITERATIONS + 1):
            # ----------------------------------------------------------
            # Anthropic path (skipped once we have fallen back)
            # ----------------------------------------------------------
            if not use_openai:
                try:
                    response = _client.messages.create(
                        model=self.model,
                        max_tokens=1024,
                        system=full_system,
                        tools=anthropic_tools if anthropic_tools else anthropic.NOT_GIVEN,
                        messages=api_messages,
                    )
                except anthropic.RateLimitError:
                    # SDK already retried (default max_retries=2) - fall back.
                    logger.warning(
                        "Specialist({cat}): Anthropic RateLimitError on iter={i}, "
                        "switching to OpenAI fallback. ticket={id}",
                        cat=self.category,
                        i=iteration,
                        id=ticket_id,
                    )
                    use_openai = True
                    provider = "openai_fallback"
                    oai_messages = _to_oai_messages(api_messages)
                except anthropic.APIStatusError as exc:
                    if exc.status_code in _FALLBACK_STATUS_CODES:
                        logger.warning(
                            "Specialist({cat}): Anthropic {code} on iter={i}, "
                            "switching to OpenAI fallback. ticket={id}",
                            cat=self.category,
                            code=exc.status_code,
                            i=iteration,
                            id=ticket_id,
                        )
                        use_openai = True
                        provider = "openai_fallback"
                        oai_messages = _to_oai_messages(api_messages)
                    else:
                        raise
                except anthropic.APITimeoutError:
                    logger.warning(
                        "Specialist({cat}): Anthropic APITimeoutError on iter={i}, "
                        "switching to OpenAI fallback. ticket={id}",
                        cat=self.category,
                        i=iteration,
                        id=ticket_id,
                    )
                    use_openai = True
                    provider = "openai_fallback"
                    oai_messages = _to_oai_messages(api_messages)
                else:
                    # Anthropic succeeded - handle its response and continue the loop.
                    logger.debug(
                        "Specialist({cat}) iter={i}/{max} stop={r} out_tokens={t}",
                        cat=self.category,
                        i=iteration,
                        max=_MAX_ITERATIONS,
                        r=response.stop_reason,
                        t=response.usage.output_tokens,
                    )

                    if response.stop_reason == "end_turn":
                        draft_response = next(
                            (b.text for b in response.content if b.type == "text"), ""
                        )
                        break

                    if response.stop_reason != "tool_use":
                        logger.warning(
                            "Specialist({cat}): unexpected stop_reason={r}, escalating",
                            cat=self.category,
                            r=response.stop_reason,
                        )
                        escalated = True
                        break

                    # stop_reason == "tool_use": execute tools and loop.
                    api_messages.append({"role": "assistant", "content": response.content})
                    tool_result_blocks: list[dict[str, Any]] = []

                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        tool = next((t for t in self.tools if t.name == block.name), None)
                        if tool is None:
                            logger.warning(
                                "Specialist({cat}): model called unknown tool '{name}'",
                                cat=self.category,
                                name=block.name,
                            )
                            result_str = json.dumps({"error": f"Tool '{block.name}' not available"})
                        else:
                            try:
                                result_obj = _cb.call(
                                    block.name,
                                    lambda _t=tool, _b=block: _t.invoke(_b.input),
                                )
                                result_str = _serialize_result(result_obj)
                                tool_results[block.name] = result_obj
                                logger.debug(
                                    "Specialist({cat}): tool={name} result_len={l}",
                                    cat=self.category,
                                    name=block.name,
                                    l=len(result_str),
                                )
                            except CircuitOpenError:
                                logger.warning(
                                    "Specialist({cat}): circuit open for tool={name}",
                                    cat=self.category,
                                    name=block.name,
                                )
                                result_str = _open_msg(block.name)
                            except Exception as tool_exc:
                                logger.error(
                                    "Specialist({cat}): tool={name} raised {e}",
                                    cat=self.category,
                                    name=block.name,
                                    e=str(tool_exc),
                                )
                                result_str = json.dumps({"error": str(tool_exc)})

                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_str,
                            }
                        )

                    api_messages.append({"role": "user", "content": tool_result_blocks})
                    continue  # next iteration, still using Anthropic

            # ----------------------------------------------------------
            # OpenAI fallback path
            # Reached when: fallback fired this iteration (after except),
            # or use_openai was already True at the top of this iteration.
            # ----------------------------------------------------------
            if use_openai:
                oai_kwargs: dict[str, Any] = {
                    "model": _OPENAI_FALLBACK_MODEL,
                    "messages": oai_messages,
                }
                if oai_tools:
                    oai_kwargs["tools"] = oai_tools

                oai_response = _oai_client.chat.completions.create(**oai_kwargs)
                choice = oai_response.choices[0]

                logger.debug(
                    "Specialist({cat}) OAI iter={i}/{max} finish={r}",
                    cat=self.category,
                    i=iteration,
                    max=_MAX_ITERATIONS,
                    r=choice.finish_reason,
                )

                if choice.finish_reason == "stop":
                    draft_response = choice.message.content or ""
                    break

                if choice.finish_reason == "tool_calls":
                    # Append assistant turn with tool_calls to oai_messages.
                    oai_messages.append(
                        {
                            "role": "assistant",
                            "content": choice.message.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments,
                                    },
                                }
                                for tc in choice.message.tool_calls
                            ],
                        }
                    )
                    for tc in choice.message.tool_calls:
                        tool = next((t for t in self.tools if t.name == tc.function.name), None)
                        if tool is None:
                            result_str = json.dumps(
                                {"error": f"Tool '{tc.function.name}' not available"}
                            )
                        else:
                            try:
                                result_obj = _cb.call(
                                    tc.function.name,
                                    lambda _t=tool, _tc=tc: _t.invoke(
                                        json.loads(_tc.function.arguments)
                                    ),
                                )
                                result_str = _serialize_result(result_obj)
                                tool_results[tc.function.name] = result_obj
                            except CircuitOpenError:
                                logger.warning(
                                    "Specialist({cat}): circuit open for tool={name}",
                                    cat=self.category,
                                    name=tc.function.name,
                                )
                                result_str = _open_msg(tc.function.name)
                            except Exception as tool_exc:
                                logger.error(
                                    "Specialist({cat}): OAI tool={name} raised {e}",
                                    cat=self.category,
                                    name=tc.function.name,
                                    e=str(tool_exc),
                                )
                                result_str = json.dumps({"error": str(tool_exc)})
                        oai_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result_str,
                            }
                        )
                    continue  # next iteration, still using OpenAI

                logger.warning(
                    "Specialist({cat}): OAI unexpected finish_reason={r}, escalating",
                    cat=self.category,
                    r=choice.finish_reason,
                )
                escalated = True
                break

        else:
            # for-loop exhausted all iterations without a break.
            logger.warning(
                "Specialist({cat}): hit {n}-iteration cap for ticket={id}, escalating",
                cat=self.category,
                n=_MAX_ITERATIONS,
                id=ticket_id,
            )
            escalated = True

        if escalated:
            return {
                "context_docs": context_docs,
                "tool_results": tool_results,
                "provider": provider,
                "escalate": True,
                "escalation_reason": (
                    f"Specialist({self.category}) could not produce a response "
                    f"within {_MAX_ITERATIONS} iterations"
                ),
                "messages": [],
            }

        logger.info(
            "Specialist({cat}): ticket={id} draft_len={l} tool_calls={tc} provider={p}",
            cat=self.category,
            id=ticket_id,
            l=len(draft_response),
            tc=len(tool_results),
            p=provider,
        )

        return {
            "context_docs": context_docs,
            "tool_results": tool_results,
            "draft_response": draft_response,
            "provider": provider,
            "messages": [AIMessage(content=draft_response)],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system(self, context_docs: list[ChunkWithScore]) -> str:
        """Combine the subclass system prompt with formatted retrieval context."""
        if not context_docs:
            return (
                self.system_prompt
                + "\n\nIMPORTANT: No policy documents were retrieved for this query. "
                "Do not invent or assume policy details. If you cannot answer from "
                "general knowledge alone, say so clearly and recommend escalation."
            )

        sections = "\n\n## Relevant policy context\n"
        for i, doc in enumerate(context_docs, 1):
            sections += (
                f"\n### [{i}] {doc.chunk.source_file}  (relevance score: {doc.score:.2f})\n"
                f"{doc.chunk.content}\n"
            )

        return self.system_prompt + sections
