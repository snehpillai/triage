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
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from loguru import logger

from triage.config import settings
from triage.graph.state import TicketState
from triage.retrieval.retriever import retrieve
from triage.retrieval.types import ChunkWithScore

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

# Cap agentic loop iterations. Hitting the cap is a symptom of a broken tool
# or a prompt that keeps asking for more data, so we escalate rather than loop.
_MAX_ITERATIONS = 5


def _to_anthropic_tool(t: BaseTool) -> dict[str, Any]:
    """Convert a LangChain @tool to the dict format Anthropic's API expects."""
    return {
        "name": t.name,
        "description": t.description or "",
        "input_schema": t.args_schema.model_json_schema(),
    }


def _serialize_result(result: Any) -> str:
    """Serialize a tool return value to a JSON string for the tool_result block."""
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, str):
        return result
    return json.dumps(result)


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
    # Public entry point — called as a LangGraph node
    # ------------------------------------------------------------------

    def run(self, state: TicketState) -> dict[str, Any]:
        """Retrieve context, run the agentic loop, and return a state update dict."""
        ticket_id = state["ticket_id"]
        content = state["content"]

        # 1. Retrieve top-5 relevant policy chunks filtered to this specialist's domain.
        context_docs = retrieve(content, category=self.category, top_k=5)
        logger.debug(
            "Specialist({cat}): retrieved {n} chunks, top_score={s:.3f}",
            cat=self.category,
            n=len(context_docs),
            s=context_docs[0].score if context_docs else 0.0,
        )

        # 2. Build the full system prompt: base instructions + formatted policy chunks.
        full_system = self._build_system(context_docs)

        # 3. Convert tools to the dict format required by the Anthropic API.
        anthropic_tools = [_to_anthropic_tool(t) for t in self.tools]

        # 4. Agentic loop.
        api_messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        tool_results: dict[str, Any] = {}
        draft_response = ""
        escalated = False

        for iteration in range(1, _MAX_ITERATIONS + 1):
            response = _client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=full_system,
                tools=anthropic_tools if anthropic_tools else anthropic.NOT_GIVEN,
                messages=api_messages,
            )

            logger.debug(
                "Specialist({cat}) iter={i}/{max} stop={r} out_tokens={t}",
                cat=self.category,
                i=iteration,
                max=_MAX_ITERATIONS,
                r=response.stop_reason,
                t=response.usage.output_tokens,
            )

            if response.stop_reason == "end_turn":
                # Extract the first text block as the draft response.
                draft_response = next((b.text for b in response.content if b.type == "text"), "")
                break

            if response.stop_reason != "tool_use":
                logger.warning(
                    "Specialist({cat}): unexpected stop_reason={r}, escalating",
                    cat=self.category,
                    r=response.stop_reason,
                )
                escalated = True
                break

            # stop_reason == "tool_use": execute each tool and loop back.
            # Append the assistant's full turn (may include a text preamble + tool blocks).
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
                        result_obj = tool.invoke(block.input)
                        result_str = _serialize_result(result_obj)
                        # Keep the structured object in state; the API only needs the string.
                        tool_results[block.name] = result_obj
                        logger.debug(
                            "Specialist({cat}): tool={name} result_len={l}",
                            cat=self.category,
                            name=block.name,
                            l=len(result_str),
                        )
                    except Exception as exc:
                        logger.error(
                            "Specialist({cat}): tool={name} raised {e}",
                            cat=self.category,
                            name=block.name,
                            e=str(exc),
                        )
                        result_str = json.dumps({"error": str(exc)})

                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    }
                )

            api_messages.append({"role": "user", "content": tool_result_blocks})

        else:
            # for-loop exhausted all iterations without a break (no end_turn reached).
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
                "escalate": True,
                "escalation_reason": (
                    f"Specialist({self.category}) could not produce a response "
                    f"within {_MAX_ITERATIONS} iterations"
                ),
                "messages": [],
            }

        logger.info(
            "Specialist({cat}): ticket={id} draft_len={l} tool_calls={tc}",
            cat=self.category,
            id=ticket_id,
            l=len(draft_response),
            tc=len(tool_results),
        )

        return {
            "context_docs": context_docs,
            "tool_results": tool_results,
            "draft_response": draft_response,
            "messages": [AIMessage(content=draft_response)],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system(self, context_docs: list[ChunkWithScore]) -> str:
        """Combine the subclass system prompt with formatted retrieval context."""
        if not context_docs:
            # Retrieval returned nothing - tell the model not to fabricate policy.
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
