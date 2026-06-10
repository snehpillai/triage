"""Smoke test: verify Anthropic API key and basic SDK usage."""

import sys

import anthropic
from loguru import logger

sys.path.insert(0, "src")
from triage.config import settings


def main() -> None:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    logger.info("Sending test message to Claude ({model})", model=settings.specialist_model)

    message = client.messages.create(
        model=settings.specialist_model,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": (
                    "You are a customer support agent. "
                    "Reply in one sentence: what is this system designed to do?"
                ),
            }
        ],
    )

    response_text = message.content[0].text
    logger.success("Response: {text}", text=response_text)
    logger.info(
        "Usage - input tokens: {i}, output tokens: {o}",
        i=message.usage.input_tokens,
        o=message.usage.output_tokens,
    )


if __name__ == "__main__":
    main()
